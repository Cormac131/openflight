/* On-chip ball track and cell selection. See track_select.h.
 *
 * Arithmetic follows the host so the parity test can demand identical cells:
 * gate statistics stay in float32 like the float32 power NumPy 2 searches,
 * and the range-walk fit runs in double like the host's float64 arrays.
 */
#include "track_select.h"

#include <math.h>
#include <string.h>

/* Mirrors of the host constants. test_iwr6843_track_select.py fails when a
 * Python constant moves without this table. */
void l3track_default_params(L3TrackParams *params)
{
    memset(params, 0, sizeof(*params));
    params->ballGates[0].loM = 2.25;     /* tracking.BALL_GATES_M */
    params->ballGates[0].hiM = 3.75;
    params->ballGates[1].loM = 3.75;
    params->ballGates[1].hiM = 5.5;
    params->nBallGates = 2U;
    params->maxRangeM = 0.0;
    params->clubGate.loM = 0.0;
    params->clubGate.hiM = 0.0;
    params->snrMin = 4.0;                /* tracking._detections snr_min */
    params->speedMinMs = 20.0;           /* tracking.SPEED_BOUNDS_MS */
    params->speedMaxMs = 90.0;
    params->fastTrackMs = 26.5;          /* tracking.FAST_TRACK_MS */
    params->fastSupportFrac = 0.55;      /* tracking.FAST_SUPPORT_FRAC */
    params->minPairDtS = 3e-3;
    params->cellPadS = 2e-3;             /* sparse.track_cells */
    params->iterations = 2500U;
    params->minDetections = 8U;
    params->cellMargin = 1U;             /* sparse.track_cells margin */
    params->clubMargin = 1U;             /* runtime.plan_sparse_cells */
}

double l3track_inlier_tol(uint32_t maxBins)
{
    return (maxBins >= 128U) ? 1.2 : 0.8;
}

double l3track_round(double value)
{
    double lower = floor(value);
    double frac = value - lower;

    if (frac > 0.5) {
        return lower + 1.0;
    }
    if (frac < 0.5) {
        return lower;
    }
    return (fmod(lower, 2.0) == 0.0) ? lower : lower + 1.0;
}

static float l3track_median(const float *values, uint32_t count, float *scratch)
{
    uint32_t i;

    memcpy(scratch, values, count * sizeof(float));
    for (i = 1U; i < count; i++) {
        float key = scratch[i];
        uint32_t j = i;
        while (j > 0U && scratch[j - 1U] > key) {
            scratch[j] = scratch[j - 1U];
            j--;
        }
        scratch[j] = key;
    }
    if ((count & 1U) != 0U) {
        return scratch[count / 2U];
    }
    return (scratch[count / 2U - 1U] + scratch[count / 2U]) / 2.0F;
}

/* One gate on one row: the strongest SNR-gated peak, sub-bin refined.
 * Returns 1 and the absolute bin when the gate holds a detection. */
static int32_t l3track_detect(const float *row, uint32_t frameStart,
                              uint32_t frameCount, double rangeResM,
                              double loM, double hiM, double snrMin,
                              float *scratch, float *absoluteBin)
{
    int32_t absLo;
    int32_t absHi;
    int32_t gLo;
    int32_t gHi;
    int32_t idx;
    int32_t k;
    float base;
    float peak;
    float local;

    if (hiM <= loM) {
        return 0;
    }
    absLo = (int32_t)(loM / rangeResM);
    absHi = (int32_t)(hiM / rangeResM);
    gLo = absLo - (int32_t)frameStart;
    if (gLo < 0) {
        gLo = 0;
    }
    gHi = absHi - (int32_t)frameStart;
    if (gHi > (int32_t)frameCount - 2) {
        gHi = (int32_t)frameCount - 2;
    }
    if (gHi - gLo < 3) {
        return 0;
    }
    base = l3track_median(&row[gLo], (uint32_t)(gHi - gLo), scratch) + 1e-12F;
    idx = gLo;
    for (k = gLo + 1; k < gHi; k++) {
        if (row[k] > row[idx]) {
            idx = k;
        }
    }
    peak = row[idx] / base;
    if ((double)peak <= snrMin) {
        return 0;
    }
    local = (float)idx;
    if (gLo < idx && idx < gHi - 1) {
        float y0 = row[idx - 1];
        float y1 = row[idx];
        float y2 = row[idx + 1];
        float den = (y0 - 2.0F * y1) + y2;
        if (den < 0.0F) {
            float offset = (y0 - y2) / (2.0F * den);
            if (fabsf(offset) < 1.0F) {
                local = local + offset;
                *absoluteBin = (float)frameStart + local;
                return 1;
            }
        }
    }
    *absoluteBin = (float)((double)frameStart + (double)local);
    return 1;
}

static double l3track_time(const L3TrackLayout *layout, uint32_t row)
{
    uint32_t frame = row / layout->nLoops;
    uint32_t loop = row % layout->nLoops;

    return (double)frame * layout->framePeriodS + (double)loop * layout->loopPeriodS;
}

static void l3track_markCell(const L3TrackLayout *layout, L3TrackWorkspace *ws,
                             uint32_t frame, int32_t absoluteBin)
{
    int32_t local = absoluteBin - (int32_t)layout->binStarts[frame];

    if (local >= 0 && local < (int32_t)layout->binCounts[frame]) {
        ws->cellMask[frame] |= ((uint64_t)1U) << (uint32_t)local;
    }
}

static uint8_t l3track_uniform(const L3TrackLayout *layout)
{
    uint32_t frame;

    for (frame = 0U; frame < layout->nFrames; frame++) {
        if (layout->binStarts[frame] != layout->binStarts[0] ||
            layout->binCounts[frame] != layout->maxBins) {
            return 0U;
        }
    }
    return 1U;
}

/* Scan every row once: store ball-gate detections per gate, and mark
 * club-gate peaks straight into the cell mask. */
static void l3track_scan(const L3TrackLayout *layout, const L3TrackParams *params,
                         L3TrackRowFn rowFn, void *rowCtx, L3TrackWorkspace *ws)
{
    uint32_t nRows = layout->nFrames * layout->nLoops;
    uint32_t row;
    uint32_t gate;

    for (row = 0U; row < nRows; row++) {
        uint32_t frame = row / layout->nLoops;
        uint32_t start = layout->binStarts[frame];
        uint32_t count = layout->binCounts[frame];
        float bin;

        memset(ws->row, 0, sizeof(ws->row));
        rowFn(rowCtx, frame, row % layout->nLoops, ws->row, count);
        for (gate = 0U; gate < params->nBallGates; gate++) {
            double hiM = params->ballGates[gate].hiM;
            if (params->maxRangeM > 0.0 && params->maxRangeM < hiM) {
                hiM = params->maxRangeM;
            }
            if (l3track_detect(ws->row, start, count, layout->rangeResM,
                               params->ballGates[gate].loM, hiM, params->snrMin,
                               ws->scratch, &bin)) {
                uint32_t slot = gate * L3T_MAX_ROWS + ws->detCount[gate];
                ws->detRow[slot] = (uint16_t)row;
                ws->detBin[slot] = bin;
                ws->detCount[gate]++;
            }
        }
        if (l3track_detect(ws->row, start, count, layout->rangeResM,
                           params->clubGate.loM, params->clubGate.hiM,
                           params->snrMin, ws->scratch, &bin)) {
            int32_t center = (int32_t)l3track_round((double)bin);
            int32_t offset;
            for (offset = -(int32_t)params->clubMargin;
                 offset <= (int32_t)params->clubMargin; offset++) {
                l3track_markCell(layout, ws, frame, center + offset);
            }
        }
    }
}

/* Put detections in the host's order: gate-major for a uniform window
 * (the host's vectorised path), row-major otherwise. */
static void l3track_order(const L3TrackLayout *layout, const L3TrackParams *params,
                          L3TrackWorkspace *ws)
{
    uint32_t gate;
    uint32_t k;

    ws->nOrder = 0U;
    if (l3track_uniform(layout)) {
        for (gate = 0U; gate < params->nBallGates; gate++) {
            for (k = 0U; k < ws->detCount[gate]; k++) {
                ws->order[ws->nOrder++] = (uint16_t)(gate * L3T_MAX_ROWS + k);
            }
        }
        return;
    }
    {
        uint32_t next[L3T_MAX_BALL_GATES] = {0U};
        for (;;) {
            uint32_t pick = params->nBallGates;
            for (gate = 0U; gate < params->nBallGates; gate++) {
                if (next[gate] >= ws->detCount[gate]) {
                    continue;
                }
                if (pick == params->nBallGates ||
                    ws->detRow[gate * L3T_MAX_ROWS + next[gate]] <
                        ws->detRow[pick * L3T_MAX_ROWS + next[pick]]) {
                    pick = gate;
                }
            }
            if (pick == params->nBallGates) {
                break;
            }
            ws->order[ws->nOrder++] = (uint16_t)(pick * L3T_MAX_ROWS + next[pick]);
            next[pick]++;
        }
    }
}

typedef struct {
    uint32_t nInliers;
    double   slope;
    double   intercept;
    double   rms;
    double   tFirst;
    double   tLast;
} L3TrackCandidate;

static double l3track_detTime(const L3TrackLayout *layout, const L3TrackWorkspace *ws,
                              uint32_t k)
{
    return l3track_time(layout, ws->detRow[ws->order[k]]);
}

static double l3track_detBin(const L3TrackWorkspace *ws, uint32_t k)
{
    return (double)ws->detBin[ws->order[k]];
}

/* Least-squares refit of the inliers of (slope, intercept). */
static void l3track_refit(const L3TrackLayout *layout, const L3TrackWorkspace *ws,
                          double slope, double intercept, double tol,
                          L3TrackCandidate *cand)
{
    uint32_t k;
    uint32_t n = 0U;
    double sumT = 0.0;
    double sumB = 0.0;
    double meanT;
    double meanB;
    double sxx = 0.0;
    double sxy = 0.0;
    double sse = 0.0;

    cand->tFirst = 0.0;
    cand->tLast = 0.0;
    for (k = 0U; k < ws->nOrder; k++) {
        double t = l3track_detTime(layout, ws, k);
        double b = l3track_detBin(ws, k);
        if (fabs(b - (slope * t + intercept)) < tol) {
            if (n == 0U || t < cand->tFirst) {
                cand->tFirst = t;
            }
            if (n == 0U || t > cand->tLast) {
                cand->tLast = t;
            }
            sumT += t;
            sumB += b;
            n++;
        }
    }
    meanT = sumT / (double)n;
    meanB = sumB / (double)n;
    for (k = 0U; k < ws->nOrder; k++) {
        double t = l3track_detTime(layout, ws, k);
        double b = l3track_detBin(ws, k);
        if (fabs(b - (slope * t + intercept)) < tol) {
            sxx += (t - meanT) * (t - meanT);
            sxy += (t - meanT) * (b - meanB);
        }
    }
    cand->nInliers = n;
    cand->slope = sxy / sxx;
    cand->intercept = meanB - cand->slope * meanT;
    for (k = 0U; k < ws->nOrder; k++) {
        double t = l3track_detTime(layout, ws, k);
        double b = l3track_detBin(ws, k);
        if (fabs(b - (slope * t + intercept)) < tol) {
            double resid = b - (cand->slope * t + cand->intercept);
            sse += resid * resid;
        }
    }
    cand->rms = sqrt(sse / (double)n);
}

/* RANSAC range walk with fastest-credible selection (find_ball_from_power). */
static uint8_t l3track_fit(const L3TrackLayout *layout, const L3TrackParams *params,
                           L3TrackPairFn pairFn, void *pairCtx,
                           const L3TrackWorkspace *ws, L3TrackCandidate *pick)
{
    double res = layout->rangeResM;
    double tol = l3track_inlier_tol(layout->maxBins);
    uint8_t haveBest = 0U;
    uint8_t haveFast = 0U;
    L3TrackCandidate best;
    L3TrackCandidate fast;
    uint32_t it;

    memset(&best, 0, sizeof(best));
    memset(&fast, 0, sizeof(fast));
    if (ws->nOrder < params->minDetections || ws->nOrder < 2U) {
        return 0U;
    }
    for (it = 0U; it < params->iterations; it++) {
        uint32_t i;
        uint32_t j;
        uint32_t k;
        uint32_t n = 0U;
        double dT;
        double slope;
        double intercept;
        uint8_t beatsBest;
        uint8_t beatsFast;
        L3TrackCandidate cand;

        pairFn(pairCtx, ws->nOrder, &i, &j);
        dT = l3track_detTime(layout, ws, i) - l3track_detTime(layout, ws, j);
        if (fabs(dT) < params->minPairDtS) {
            continue;
        }
        slope = (l3track_detBin(ws, i) - l3track_detBin(ws, j)) / dT;
        if (!(params->speedMinMs <= slope * res && slope * res <= params->speedMaxMs)) {
            continue;
        }
        intercept = l3track_detBin(ws, i) - slope * l3track_detTime(layout, ws, i);
        for (k = 0U; k < ws->nOrder; k++) {
            double t = l3track_detTime(layout, ws, k);
            if (fabs(l3track_detBin(ws, k) - (slope * t + intercept)) < tol) {
                n++;
            }
        }
        if (n < params->minDetections) {
            continue;
        }
        beatsBest = (!haveBest || n > best.nInliers) ? 1U : 0U;
        beatsFast = (slope * res >= params->fastTrackMs &&
                     (!haveFast || n > fast.nInliers)) ? 1U : 0U;
        if (!beatsBest && !beatsFast) {
            continue;
        }
        l3track_refit(layout, ws, slope, intercept, tol, &cand);
        if (!(params->speedMinMs <= cand.slope * res &&
              cand.slope * res <= params->speedMaxMs)) {
            continue;
        }
        if (beatsBest) {
            best = cand;
            haveBest = 1U;
        }
        if (cand.slope * res >= params->fastTrackMs &&
            (!haveFast || n > fast.nInliers)) {
            fast = cand;
            haveFast = 1U;
        }
    }
    if (!haveBest) {
        return 0U;
    }
    *pick = best;
    if (haveFast && best.slope * res < params->fastTrackMs &&
        (double)fast.nInliers >= params->fastSupportFrac * (double)best.nInliers) {
        *pick = fast;
    }
    return 1U;
}

static void l3track_markTrack(const L3TrackLayout *layout, const L3TrackParams *params,
                              const L3TrackCandidate *track, L3TrackWorkspace *ws)
{
    uint32_t frame;
    uint32_t loop;

    for (frame = 0U; frame < layout->nFrames; frame++) {
        for (loop = 0U; loop < layout->nLoops; loop++) {
            double t = l3track_time(layout, frame * layout->nLoops + loop);
            int32_t center;
            int32_t offset;
            if (t < track->tFirst - params->cellPadS || t > track->tLast + params->cellPadS) {
                continue;
            }
            center = (int32_t)l3track_round(track->slope * t + track->intercept);
            for (offset = -(int32_t)params->cellMargin;
                 offset <= (int32_t)params->cellMargin; offset++) {
                l3track_markCell(layout, ws, frame, center + offset);
            }
        }
    }
}

int32_t l3track_select(const L3TrackLayout *layout,
                       const L3TrackParams *params,
                       L3TrackRowFn rowFn, void *rowCtx,
                       L3TrackPairFn pairFn, void *pairCtx,
                       L3TrackWorkspace *ws,
                       L3TrackResult *result)
{
    uint32_t frame;
    int32_t cells = 0;
    L3TrackCandidate track;

    memset(result, 0, sizeof(*result));
    if (layout->nFrames == 0U || layout->nFrames > L3T_MAX_FRAMES ||
        layout->nLoops == 0U || layout->nLoops > L3T_MAX_LOOPS ||
        layout->maxBins > L3T_MAX_BINS || params->nBallGates > L3T_MAX_BALL_GATES ||
        layout->rangeResM <= 0.0) {
        return -1;
    }
    for (frame = 0U; frame < layout->nFrames; frame++) {
        if (layout->binCounts[frame] > layout->maxBins) {
            return -1;
        }
    }
    memset(ws->detCount, 0, sizeof(ws->detCount));
    memset(ws->cellMask, 0, sizeof(ws->cellMask));
    l3track_scan(layout, params, rowFn, rowCtx, ws);
    l3track_order(layout, params, ws);
    if (l3track_fit(layout, params, pairFn, pairCtx, ws, &track)) {
        result->found = 1U;
        result->nInliers = track.nInliers;
        result->slopeBins = track.slope;
        result->interceptBins = track.intercept;
        result->rmsBins = track.rms;
        result->tFirstS = track.tFirst;
        result->tLastS = track.tLast;
        l3track_markTrack(layout, params, &track, ws);
    }
    for (frame = 0U; frame < layout->nFrames; frame++) {
        uint64_t mask = ws->cellMask[frame];
        while (mask != 0U) {
            mask &= mask - 1U;
            cells++;
        }
    }
    return cells;
}

void l3track_rng_seed(L3TrackRng *rng, uint32_t seed)
{
    rng->state = (seed != 0U) ? seed : 1U;
}

static uint32_t l3track_rng_next(L3TrackRng *rng)
{
    uint32_t x = rng->state;

    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    rng->state = x;
    return x;
}

void l3track_rng_pair(void *ctx, uint32_t n, uint32_t *i, uint32_t *j)
{
    L3TrackRng *rng = (L3TrackRng *)ctx;

    *i = l3track_rng_next(rng) % n;
    *j = l3track_rng_next(rng) % (n - 1U);
    if (*j >= *i) {
        (*j)++;
    }
}
