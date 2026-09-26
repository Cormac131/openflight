/* Tracking stage: ball detection + RANSAC range-walk fit. See solve_tracking.h.
 *
 * ============================================================================
 * AUDIT (Task 5 of the on-chip solve plan): track_select.c vs. tracking.py
 * ============================================================================
 *
 * The plan's recipe said to port tracking.py from scratch. That is wrong:
 * firmware/iwr6843/track_select.c already ports most of the same algorithm
 * for a different caller (the l3track CLI's cell-selection planner, which
 * itself calls tracking.find_ball_from_power via sparse.track_cells). This
 * file reuses what genuinely transfers, ports what does not, and writes only
 * what is genuinely new. Every divergence found during the audit, and
 * whether it matters for this stage:
 *
 * 1. REUSED UNCHANGED: l3track_inlier_tol() (n_samples>=128 ? 1.2 : 0.8), a
 *    pure function independent of track_select.h's buffer-size macros, so
 *    it calls directly into track_select.c with no wrapper.
 *    l3track_rng_seed()/l3track_rng_pair() (the xorshift32 pair source) were
 *    ALSO reused unchanged in this stage's first draft, on the assumption
 *    that reusing track_select.c's RNG rather than inventing a second one
 *    would give bit-exact parity with tracking.find_ball()'s numpy-driven
 *    reference. Running the corpus proved that assumption wrong: xorshift32
 *    is not numpy's PCG64/Generator algorithm, and l3track_rng_pair
 *    mismatched n_inliers by 1 on 3 of the 15 corpus cases when used as the
 *    on-chip draw source -- a plausible RANSAC winner, just not the exact
 *    one. That gap (up to rel=0.05 on speed_ms/n_inliers, per the corpus's
 *    borderline cases) is far outside any launch-monitor spec, so it is not
 *    an acceptable "on-chip matches the reference" story by itself.
 *    THE FIX, not just a caveat: solve_numpy_rng.c (new, this task) is a
 *    from-scratch port of numpy's SeedSequence + PCG64 + Generator.choice(n,
 *    2, replace=False) -- the exact algorithm tracking.py's RANSAC loop
 *    calls -- verified bit-for-bit identical to numpy 2.4.6 across 7 seeds,
 *    15 n values spanning this stage's real nOrder range (78-249) and
 *    beyond, and full 2500-iteration draw sequences (see
 *    tests/test_iwr6843_solve_numpy_rng.py). solve_numpy_rng_pair() is now
 *    THIS STAGE'S RECOMMENDED on-chip pairFn -- not l3track_rng_pair -- and
 *    Tasks 6-8's stages (which port the same find_ball_from_power()-style
 *    RANSAC pattern) should use it too, not re-derive or reuse xorshift32.
 *    l3track_rng_pair remains exactly what it always was for
 *    track_select.c's OWN caller (the l3track CLI): a deterministic,
 *    dependency-free draw source that does not claim bit-exactness. This
 *    stage no longer includes it via track_select.h's RNG entry points for
 *    correctness reasons, only l3track_inlier_tol -- see the updated build
 *    wiring below and test_iwr6843_solve_tracking.py's own two RNG tests
 *    (the bit-exact one against solve_numpy_rng, and a separate,
 *    still-approximate one that keeps exercising l3track_rng_pair for
 *    whoever might still want a numpy-free fallback).
 *    A SEPARATE, NARROWER CORRECTION, found during this same review: this
 *    banner previously called n_inliers exactness (in the numpy-draws
 *    equivalence test) a guaranteed consequence of "both sides run the
 *    identical median/argmax/parabola arithmetic in float64". That is
 *    wrong. loop_power() is NOT bit-identical to numpy's reference even at
 *    identical draws: numpy's np.abs() on complex128 computes hypot(re,im)
 *    then squares it, this port computes re*re+im*im directly, and numpy
 *    reduces the TX/RX sum pairwise over two array axes while this port
 *    accumulates it in a single sequential loop -- both real, structural
 *    floating-point divergences (see solve_tracking_loop_power() below),
 *    not summation-order noise alone. n_inliers landing exactly right is
 *    therefore MEASURED across the corpus's 15 cases (it does, for both the
 *    exact-numpy-draws test and the new bit-exact-RNG test below), not
 *    something the arithmetic *guarantees* -- the inlier count is a hard
 *    `< tol` boundary test, so a detection sitting close enough to that
 *    boundary could in principle flip under this divergence. See
 *    tests/test_iwr6843_solve_tracking.py's own tolerance-table comment for
 *    the matching correction on the test side.
 *
 * 2. NOT REUSABLE AS-IS: track_select.h's fixed buffers (L3T_MAX_BINS=64,
 *    float32 row/scratch/detBin arrays) do not fit this stage. L3T_MAX_BINS
 *    is sized for the l3track CLI's narrower ring-window debug capture
 *    (L3_RING_MAX_BINS); the solve stage's shot captures always run the
 *    full n_samples=128 range FFT (confirmed against every case in
 *    tests/golden/iwr6843/tracking/). Calling l3track_select() with
 *    nBins=128 would write past ws->row[64]/ws->scratch[64] -- a real
 *    buffer overrun, not a style preference. track_select.c's own
 *    float32 arithmetic ("gate statistics stay in float32 like the float32
 *    power NumPy 2 searches", per its file banner) is also a deliberate
 *    choice for ITS caller (sparse.py's own float32 power representation),
 *    not for tracking.py, which computes loop_power() and _detections() in
 *    numpy's default float64. Task 5's tolerance table calls for exact bin
 *    positions and inlier counts, so this file uses double throughout
 *    (matching tracking.py's actual arithmetic) rather than inheriting
 *    track_select.c's float32 choice -- a real precision divergence between
 *    the two files, deliberate on both sides for their own callers.
 *    CONSEQUENCE: the detection/order/fit/refit logic below is a fresh
 *    port (new code), structurally mirroring track_select.c's
 *    l3track_scan/l3track_order/l3track_fit/l3track_refit (which are
 *    `static`, not exported, so literally could not be called from here
 *    even if the sizes matched) at double precision and the larger sizes.
 *    track_select.c itself is NOT modified -- it stays exactly as shipped
 *    for the l3track command.
 *
 * 3. HEADER CLAIM CHECKED, FOUND WRONG: track_select.h's banner says it is
 *    a port "without the quadratic refit". track_select.c in fact has NO
 *    quadratic (degree-2) refit -- there are two distinct refits in
 *    tracking.find_ball_from_power(): a LINEAR least-squares refit of the
 *    picked candidate's inliers (tracking.py:386-389), which track_select.c
 *    DOES port as l3track_refit(); and a QUADRATIC polyfit for the LOCAL
 *    radial speed (tracking.py:407-413, BallTrack.quad_bins), which neither
 *    track_select.c nor this file ports. So the header's claim is correct
 *    about the quadratic refit specifically, just easy to misread as "no
 *    refit at all" -- worth stating plainly since the plan's own dispatch
 *    note flagged this as needing verification. quad_bins is not part of
 *    this stage's result (SolveTrackingResult has no quad_bins field): cell
 *    selection never reads it (per track_select.h) and the solve's own
 *    per-field tolerance table (task-5-brief.md) does not ask for it
 *    either -- see solve_tracking.h and the test file for what IS asserted.
 *
 * 4. DETECTION ORDERING -- A LATENT DIVERGENCE, NOT EXERCISED HERE:
 *    track_select.c's l3track_order() decides gate-major vs. row-major
 *    ordering by checking whether every frame's binStarts/binCounts are
 *    numerically UNIFORM (l3track_uniform()). tracking.py's _detections()
 *    decides by whether geo.range_bin_starts IS NONE, regardless of
 *    whether the values happen to be uniform. These can disagree: a
 *    windowed dump whose per-frame starts all happen to be equal would
 *    take track_select.c's gate-major branch but tracking.py's row-major
 *    branch. This matters, because RANSAC draws indices by POSITION in the
 *    detection order, so the two orderings can hand the same pair-index
 *    draws to different underlying detections. It happens not to matter for
 *    track_select.c's own caller (sparse.py's planner, a different host
 *    module with its own convention, per its test file's comment "Per-frame
 *    windows switch the host to row-major detection order" -- sparse.py
 *    apparently always supplies windowed layouts when non-uniform, uniform
 *    ones otherwise, so the two conditions coincide there). It is NOT
 *    correct for tracking.find_ball()/find_ball_from_power() in general.
 *    This file therefore branches on `layout->binStarts == NULL` (see
 *    solve_tracking_windowed() below) -- the true-to-Python condition --
 *    rather than porting l3track_uniform()'s heuristic. UNVERIFIED: no
 *    golden tracking case exercises a windowed dump (every case in
 *    tests/golden/iwr6843/tracking/ has geo_range_bin_starts empty), so
 *    this branch's correctness rests on reading tracking.py directly, not
 *    on corpus equivalence. Flagged for whoever next touches a windowed
 *    dump on this stage.
 *
 * 5. GENUINELY NEW: loop_power() itself. find_ball()'s own module docstring
 *    calls find_ball_from_power() (which track_select.c's algorithm
 *    mirrors) "one layer below" -- find_ball() is a thin wrapper that
 *    reduces the raw MTI cube to per-row residual power first
 *    (tracking.py:197-202: sum |mti|^2 over the 2 TX blocks and every RX
 *    channel). Nothing in track_select.c does this reduction -- its rowFn
 *    callback is handed already-computed power by its caller. This stage's
 *    golden vectors record the MTI cube itself (mti_re/mti_im), not power,
 *    so this reduction has to happen on this side of the boundary; see
 *    solve_tracking_loop_power() below.
 *
 * 6. NOT PORTED, BY SCOPE: time_window_s (find_ball's kwarg for restricting
 *    the search to pre-impact frames) is used only by the CLUB estimator's
 *    call into find_ball_from_power (per find_ball's own docstring) --
 *    Task 8's stage, not this one. No golden tracking case sets it. Left
 *    out of SolveTrackingParams; Task 8 adds its own struct with whatever
 *    it needs rather than this file guessing at the shape now.
 *
 * ============================================================================
 */
#include "solve_tracking.h"

#include <math.h>
#include <string.h>

#include "../track_select.h"  /* l3track_rng_seed/pair, l3track_inlier_tol -- reused, see item 1 above */

void solve_tracking_default_params(SolveTrackingParams *params)
{
    memset(params, 0, sizeof(*params));
    params->gates[0].loM = 2.25;     /* tracking.BALL_GATES_M */
    params->gates[0].hiM = 3.75;
    params->gates[1].loM = 3.75;
    params->gates[1].hiM = 5.5;
    params->nGates = 2U;
    params->maxRangeM = 0.0;
    params->snrMin = 4.0;            /* tracking._detections snr_min */
    params->speedMinMs = 20.0;        /* tracking.SPEED_BOUNDS_MS */
    params->speedMaxMs = 90.0;
    params->minBallMs = 26.5;         /* tracking.FAST_TRACK_MS */
    params->fastSupportFrac = 0.55;   /* tracking.FAST_SUPPORT_FRAC */
    params->minPairDtS = 3e-3;        /* find_ball_from_power's "abs(d_t) < 3e-3" */
    params->iterations = 2500U;       /* find_ball's iterations default */
    params->minDetections = 8U;       /* find_ball_from_power's literal 8 */
}

static uint32_t solve_tracking_frame_bin_start(const SolveTrackingLayout *layout, uint32_t frame)
{
    return (layout->binStarts != NULL) ? layout->binStarts[frame] : layout->rangeBinStart;
}

static uint32_t solve_tracking_frame_bin_count(const SolveTrackingLayout *layout, uint32_t frame)
{
    return (layout->binCounts != NULL) ? layout->binCounts[frame] : layout->nBins;
}

/* True exactly when tracking.py's Geometry.range_bin_starts is not None --
 * see audit item 4 above for why this, and not track_select.c's
 * uniformity heuristic, is the correct condition here. */
static uint8_t solve_tracking_windowed(const SolveTrackingLayout *layout)
{
    return (layout->binStarts != NULL) ? 1U : 0U;
}

/* Geometry.loop_time(): seconds from window start for (ring-slot frame, loop). */
static double solve_tracking_time(const SolveTrackingLayout *layout, uint32_t row)
{
    uint32_t frame = row / layout->nLoops;
    uint32_t loop = row % layout->nLoops;
    int32_t nFrames = (int32_t)layout->nFrames;
    int32_t diff = (int32_t)frame - (int32_t)layout->triggerFrame;
    uint32_t slotOrder = (uint32_t)(((diff % nFrames) + nFrames) % nFrames);
    double frameTime = (layout->frameTimeOffsetsS != NULL)
        ? layout->frameTimeOffsetsS[slotOrder]
        : (double)slotOrder * layout->framePeriodS;

    return frameTime + (double)loop * layout->loopPeriodS;
}

static double solve_tracking_median(const double *values, uint32_t count, double *scratch)
{
    uint32_t i;

    memcpy(scratch, values, count * sizeof(double));
    for (i = 1U; i < count; i++) {
        double key = scratch[i];
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
    return (scratch[count / 2U - 1U] + scratch[count / 2U]) / 2.0;
}

/* tracking._detections's per-row peak: strongest SNR-gated peak inside
 * [loM, hiM), sub-bin refined by the same 3-point parabola as
 * track_select.c's l3track_detect(), ported to double and to a per-frame
 * (not fixed-maxBins) frameCount. */
static int32_t solve_tracking_detect(const double *row, uint32_t frameStart, uint32_t frameCount,
                                     double rangeResM, double loM, double hiM, double snrMin,
                                     double *scratch, double *absoluteBin)
{
    int32_t absLo;
    int32_t absHi;
    int32_t gLo;
    int32_t gHi;
    int32_t idx;
    int32_t k;
    double base;
    double peak;
    double local;

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
    base = solve_tracking_median(&row[gLo], (uint32_t)(gHi - gLo), scratch) + 1e-12;
    idx = gLo;
    for (k = gLo + 1; k < gHi; k++) {
        if (row[k] > row[idx]) {
            idx = k;
        }
    }
    peak = row[idx] / base;
    if (peak <= snrMin) {
        return 0;
    }
    local = (double)idx;
    if (gLo < idx && idx < gHi - 1) {
        double y0 = row[idx - 1];
        double y1 = row[idx];
        double y2 = row[idx + 1];
        double den = (y0 - 2.0 * y1) + y2;
        if (den < 0.0) {
            double offset = (y0 - y2) / (2.0 * den);
            if (fabs(offset) < 1.0) {
                local += offset;
            }
        }
    }
    *absoluteBin = (double)frameStart + local;
    return 1;
}

/* tracking.loop_power(): MTI residual power for one (frame, loop), summed
 * over the 2 TX blocks and every RX channel. Genuinely new -- see audit
 * item 5. mtiRowFn supplies the raw complex samples (wherever they live:
 * an L3 read on the DSS, a golden-vector lookup on the host); this
 * function owns the |.|^2 reduction. */
static void solve_tracking_loop_power(SolveTrackingMtiRowFn mtiRowFn, void *ctx,
                                      uint32_t frame, uint32_t loop,
                                      uint32_t nRx, uint32_t count,
                                      SolveTrackingWorkspace *ws)
{
    uint32_t tx;
    uint32_t rx;
    uint32_t bin;

    memset(ws->row, 0, count * sizeof(double));
    mtiRowFn(ctx, frame, loop, nRx, count, &ws->mtiRow);
    for (tx = 0U; tx < 2U; tx++) {
        for (rx = 0U; rx < nRx; rx++) {
            const double *re = &ws->mtiRow.re[(tx * nRx + rx) * count];
            const double *im = &ws->mtiRow.im[(tx * nRx + rx) * count];

            for (bin = 0U; bin < count; bin++) {
                ws->row[bin] += (re[bin] * re[bin]) + (im[bin] * im[bin]);
            }
        }
    }
}

/* Scan every row once: ball-gate detections per gate, tracking._detections. */
static void solve_tracking_scan(const SolveTrackingLayout *layout, const SolveTrackingParams *params,
                                SolveTrackingMtiRowFn mtiRowFn, void *ctx, SolveTrackingWorkspace *ws)
{
    uint32_t nRows = layout->nFrames * layout->nLoops;
    uint32_t row;
    uint32_t gate;

    for (row = 0U; row < nRows; row++) {
        uint32_t frame = row / layout->nLoops;
        uint32_t loop = row % layout->nLoops;
        uint32_t start = solve_tracking_frame_bin_start(layout, frame);
        uint32_t count = solve_tracking_frame_bin_count(layout, frame);
        double bin;

        solve_tracking_loop_power(mtiRowFn, ctx, frame, loop, layout->nRx, count, ws);
        for (gate = 0U; gate < params->nGates; gate++) {
            double hiM = params->gates[gate].hiM;

            if (params->maxRangeM > 0.0 && params->maxRangeM < hiM) {
                hiM = params->maxRangeM;
            }
            if (solve_tracking_detect(ws->row, start, count, layout->rangeResM,
                                      params->gates[gate].loM, hiM, params->snrMin,
                                      ws->scratch, &bin)) {
                uint32_t slot = gate * SOLVE_TRACKING_MAX_ROWS + ws->detCount[gate];

                ws->detRow[slot] = (uint16_t)row;
                ws->detBin[slot] = bin;
                ws->detCount[gate]++;
            }
        }
    }
}

/* Put detections in tracking._detections's order: gate-major for a
 * non-windowed dump (the vectorised host path), row-major for a windowed
 * one (see audit item 4 -- this is NOT track_select.c's uniformity
 * heuristic). */
static void solve_tracking_order(const SolveTrackingLayout *layout, const SolveTrackingParams *params,
                                 SolveTrackingWorkspace *ws)
{
    uint32_t gate;
    uint32_t k;

    ws->nOrder = 0U;
    if (!solve_tracking_windowed(layout)) {
        for (gate = 0U; gate < params->nGates; gate++) {
            for (k = 0U; k < ws->detCount[gate]; k++) {
                ws->order[ws->nOrder++] = (uint16_t)(gate * SOLVE_TRACKING_MAX_ROWS + k);
            }
        }
        return;
    }
    {
        uint32_t next[SOLVE_TRACKING_MAX_GATES] = {0U};

        for (;;) {
            uint32_t pick = params->nGates;

            for (gate = 0U; gate < params->nGates; gate++) {
                if (next[gate] >= ws->detCount[gate]) {
                    continue;
                }
                if (pick == params->nGates ||
                    ws->detRow[gate * SOLVE_TRACKING_MAX_ROWS + next[gate]] <
                        ws->detRow[pick * SOLVE_TRACKING_MAX_ROWS + next[pick]]) {
                    pick = gate;
                }
            }
            if (pick == params->nGates) {
                break;
            }
            ws->order[ws->nOrder++] = (uint16_t)(pick * SOLVE_TRACKING_MAX_ROWS + next[pick]);
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
} SolveTrackingCandidate;

static double solve_tracking_det_time(const SolveTrackingLayout *layout,
                                      const SolveTrackingWorkspace *ws, uint32_t k)
{
    return solve_tracking_time(layout, ws->detRow[ws->order[k]]);
}

static double solve_tracking_det_bin(const SolveTrackingWorkspace *ws, uint32_t k)
{
    return ws->detBin[ws->order[k]];
}

/* Linear least-squares refit of the inliers of (slope, intercept) --
 * tracking.find_ball_from_power's design/lstsq block (tracking.py:386-391).
 * NOT the quadratic refit (see audit item 3): there is no analog of
 * BallTrack.quad_bins here. */
static void solve_tracking_refit(const SolveTrackingLayout *layout, const SolveTrackingWorkspace *ws,
                                 double slope, double intercept, double tol,
                                 SolveTrackingCandidate *cand)
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
        double t = solve_tracking_det_time(layout, ws, k);
        double b = solve_tracking_det_bin(ws, k);

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
        double t = solve_tracking_det_time(layout, ws, k);
        double b = solve_tracking_det_bin(ws, k);

        if (fabs(b - (slope * t + intercept)) < tol) {
            sxx += (t - meanT) * (t - meanT);
            sxy += (t - meanT) * (b - meanB);
        }
    }
    cand->nInliers = n;
    cand->slope = sxy / sxx;
    cand->intercept = meanB - cand->slope * meanT;
    for (k = 0U; k < ws->nOrder; k++) {
        double t = solve_tracking_det_time(layout, ws, k);
        double b = solve_tracking_det_bin(ws, k);

        if (fabs(b - (slope * t + intercept)) < tol) {
            double resid = b - (cand->slope * t + cand->intercept);

            sse += resid * resid;
        }
    }
    cand->rms = sqrt(sse / (double)n);
}

/* RANSAC range walk with fastest-credible selection --
 * tracking.find_ball_from_power(). Structurally mirrors track_select.c's
 * l3track_fit() (see audit item 2 for why this is a fresh port, not a call
 * into it). */
static uint8_t solve_tracking_fit(const SolveTrackingLayout *layout, const SolveTrackingParams *params,
                                  SolveTrackingPairFn pairFn, void *pairCtx,
                                  const SolveTrackingWorkspace *ws, SolveTrackingCandidate *pick)
{
    double res = layout->rangeResM;
    double tol = l3track_inlier_tol(layout->nBins);  /* reused, see audit item 1 */
    uint8_t haveBest = 0U;
    uint8_t haveFast = 0U;
    SolveTrackingCandidate best;
    SolveTrackingCandidate fast;
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
        SolveTrackingCandidate cand;

        pairFn(pairCtx, ws->nOrder, &i, &j);
        dT = solve_tracking_det_time(layout, ws, i) - solve_tracking_det_time(layout, ws, j);
        if (fabs(dT) < params->minPairDtS) {
            continue;
        }
        slope = (solve_tracking_det_bin(ws, i) - solve_tracking_det_bin(ws, j)) / dT;
        if (!(params->speedMinMs <= slope * res && slope * res <= params->speedMaxMs)) {
            continue;
        }
        intercept = solve_tracking_det_bin(ws, i) - slope * solve_tracking_det_time(layout, ws, i);
        for (k = 0U; k < ws->nOrder; k++) {
            double t = solve_tracking_det_time(layout, ws, k);

            if (fabs(solve_tracking_det_bin(ws, k) - (slope * t + intercept)) < tol) {
                n++;
            }
        }
        if (n < params->minDetections) {
            continue;
        }
        beatsBest = (!haveBest || n > best.nInliers) ? 1U : 0U;
        beatsFast = (slope * res >= params->minBallMs &&
                     (!haveFast || n > fast.nInliers)) ? 1U : 0U;
        if (!beatsBest && !beatsFast) {
            continue;
        }
        solve_tracking_refit(layout, ws, slope, intercept, tol, &cand);
        if (!(params->speedMinMs <= cand.slope * res && cand.slope * res <= params->speedMaxMs)) {
            continue;
        }
        if (beatsBest) {
            best = cand;
            haveBest = 1U;
        }
        if (cand.slope * res >= params->minBallMs && (!haveFast || n > fast.nInliers)) {
            fast = cand;
            haveFast = 1U;
        }
    }
    if (!haveBest) {
        return 0U;
    }
    *pick = best;
    if (haveFast && best.slope * res < params->minBallMs &&
        (double)fast.nInliers >= params->fastSupportFrac * (double)best.nInliers) {
        *pick = fast;
    }
    return 1U;
}

uint32_t solve_tracking_find_ball(const SolveTrackingLayout *layout,
                                   const SolveTrackingParams *params,
                                   SolveTrackingMtiRowFn mtiRowFn, void *mtiRowCtx,
                                   SolveTrackingPairFn pairFn, void *pairCtx,
                                   SolveTrackingWorkspace *ws,
                                   SolveTrackingResult *result)
{
    SolveTrackingCandidate track;

    memset(result, 0, sizeof(*result));
    if (layout->nFrames == 0U || layout->nFrames > SOLVE_TRACKING_MAX_FRAMES ||
        layout->nLoops == 0U || layout->nLoops > SOLVE_TRACKING_MAX_LOOPS ||
        layout->nBins == 0U || layout->nBins > SOLVE_TRACKING_MAX_BINS ||
        layout->nRx == 0U || layout->nRx > SOLVE_TRACKING_MAX_RX ||
        params->nGates > SOLVE_TRACKING_MAX_GATES ||
        layout->rangeResM <= 0.0) {
        return SOLVE_TRACKING_ERROR;
    }
    if (layout->binCounts != NULL) {
        uint32_t frame;

        for (frame = 0U; frame < layout->nFrames; frame++) {
            if (layout->binCounts[frame] > layout->nBins) {
                return SOLVE_TRACKING_ERROR;
            }
        }
    }
    memset(ws->detCount, 0, sizeof(ws->detCount));
    solve_tracking_scan(layout, params, mtiRowFn, mtiRowCtx, ws);
    solve_tracking_order(layout, params, ws);
    if (solve_tracking_fit(layout, params, pairFn, pairCtx, ws, &track)) {
        double span = track.tLast - track.tFirst;

        result->found = 1U;
        result->nInliers = track.nInliers;
        result->slopeBins = track.slope;
        result->interceptBins = track.intercept;
        result->rmsBins = track.rms;
        result->tFirstS = track.tFirst;
        result->tLastS = track.tLast;
        result->speedMs = track.slope * layout->rangeResM;
        /* BallTrack.low_confidence: tracking.py:423. */
        result->lowConfidence = (track.rms >= 0.45 || span < 0.012) ? 1U : 0U;
    }
    return SOLVE_TRACKING_OK;
}
