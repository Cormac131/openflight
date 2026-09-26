/* LCMF-v1 vertical launch-angle stage -- SCOPED PORT. See solve_lcmf.h. */

#include "solve_lcmf.h"

#include <math.h>
#include <string.h>

/* ---- audit: what this file reuses, extends, or leaves new -------------
 *
 * lcmf.py's estimate_lcmf_v1() (lcmf.py:766) is 962 lines including its
 * module. Reading it end to end (Task 6's Step 1) found three genuinely
 * separate decision paths inside it:
 *
 *   (a) the vertical launch-angle decision: _snapshot_cache ->
 *       _balanced_indices -> _channel_estimates (_spatial_dictionary +
 *       multipath.leave_one_channel_out_error + _frame_objective) ->
 *       combine_channels -> angle_deg/channels_used/single_channel.
 *   (b) the fast-time diagnostic models (_fast_estimates and its three
 *       helpers): computed AFTER (a) already decided angle_deg
 *       (lcmf.py:888 runs before lcmf.py:899-911), folded only into
 *       components_deg for the session log. Traced, not assumed: (a)'s
 *       combine_channels call at lcmf.py:921 reads channel_components/
 *       channel_evidence, which (b) never touches.
 *   (c) the horizontal TX2 proxy (_tx2_horizontal_proxy and its helpers):
 *       an entirely separate axis, its own output fields
 *       (horizontal_deg/horizontal_confidence/horizontal_status), no
 *       influence on (a).
 *
 * This file ports (a) only. Reused, not reinvented: solve_fft.c's window+
 * FFT primitive is NOT needed here at all -- _prepared_fft/_fast_design
 * (path (b)) are the only lcmf.py call sites that use it, and (b) is out
 * of this port's scope. tracking.BallTrack's fields feed in as
 * SolveLcmfTrack (already-found track, same convention solve_tracking.c's
 * caller supplies); nothing from solve_tracking.c itself is linked in
 * here because this stage takes the already-fitted slope/intercept
 * directly, not raw MTI rows to re-detect from.
 *
 * Genuinely new in this file (no existing solve_*.c precedent): the
 * doa.canonicalize_tx_blocks port, the Calibration.apply/true_range
 * ports, multipath.ballistic_trajectory_from_range (Newton iteration),
 * the DD/DG/GD/GG spatial dictionary construction, a complex generalized
 * pseudo-inverse via normal equations (multipath.leave_one_channel_out_
 * error), and the per-frame-median/log-mean objective + parabolic
 * grid refinement + channel combination.
 *
 * Verified empirically, not merely traced: running the Python reference
 * over all 18 corpus cases (see the task report) confirms every case
 * that reaches path (b) has it succeed with no exception -- so no corpus
 * case's status/angle_deg depends on anything this file omits. This is
 * checked into the report as measured evidence, not an assumption made
 * once and never revisited.
 * ------------------------------------------------------------------- */

/* ---- module constants (lcmf.py's frozen module-level constants) ------- */
#define LCMF_LAM_M              0.004835362225806452 /* music.LAM = C/F_C */
#define LCMF_MPH_PER_MS          2.23694
#define LCMF_MIN_SNR             8.0
#define LCMF_MAX_RANGE_M         4.7
#define LCMF_CHANNEL_SPREAD_MAX_DEG 8.0
#define LCMF_LATERAL_TEE_OFFSET_M 0.064
#define LCMF_ANGLE_CORRECTION_DEG 0.0
#define LCMF_GRAVITY_MS2          9.81
#define LCMF_GRID_LO_DEG         (-5.0)
#define LCMF_GRID_HI_DEG          45.0

/* Local pi constant rather than M_PI: -std=c99 does not guarantee M_PI is
 * declared in math.h (it is a POSIX/BSD extension), matching solve_fft.c's
 * own choice. */
#define LCMF_PI 3.14159265358979323846

/* ---- tiny complex helpers (manual re/im pairs, matching solve_fft.c's
 * own choice not to depend on C99 <complex.h> on the C674x target). ---- */
typedef struct {
    double re;
    double im;
} Cplx;

static Cplx c_make(double re, double im) { Cplx c; c.re = re; c.im = im; return c; }
static Cplx c_add(Cplx a, Cplx b) { return c_make(a.re + b.re, a.im + b.im); }
static Cplx c_sub(Cplx a, Cplx b) { return c_make(a.re - b.re, a.im - b.im); }
static Cplx c_mul(Cplx a, Cplx b) {
    return c_make(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re);
}
static Cplx c_conj(Cplx a) { return c_make(a.re, -a.im); }
static double c_abs2(Cplx a) { return a.re * a.re + a.im * a.im; }
static Cplx c_expi(double theta) { return c_make(cos(theta), sin(theta)); }
static Cplx c_div(Cplx a, Cplx b) {
    double denom = c_abs2(b);
    Cplx num = c_mul(a, c_conj(b));
    return c_make(num.re / denom, num.im / denom);
}

static double d_clip(double v, double lo, double hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/* ---- doa.canonicalize_tx_blocks (doa.py:140) --------------------------- */
static void canonicalize_tx_blocks(const Cplx early[SOLVE_LCMF_N_RX],
                                    const Cplx late[SOLVE_LCMF_N_RX],
                                    double tdmPhase, uint8_t reversed,
                                    Cplx out[SOLVE_LCMF_N_ELEMENTS]) {
    Cplx rot = c_expi(-tdmPhase);
    Cplx lateCorrected[SOLVE_LCMF_N_RX];
    Cplx concat[SOLVE_LCMF_N_ELEMENTS];
    uint32_t i;
    for (i = 0; i < SOLVE_LCMF_N_RX; i++) {
        lateCorrected[i] = c_mul(late[i], rot);
    }
    if (!reversed) {
        for (i = 0; i < SOLVE_LCMF_N_RX; i++) {
            concat[i] = early[i];
            concat[SOLVE_LCMF_N_RX + i] = lateCorrected[i];
        }
    } else {
        for (i = 0; i < SOLVE_LCMF_N_RX; i++) {
            concat[i] = lateCorrected[i];
            concat[SOLVE_LCMF_N_RX + i] = early[i];
        }
    }
    for (i = 0; i < SOLVE_LCMF_N_ELEMENTS; i++) {
        out[i] = concat[SOLVE_LCMF_N_ELEMENTS - 1U - i];
    }
}

/* ---- multipath.ballistic_trajectory_from_range (multipath.py:36) -----
 * Per-element port: numpy's version is applied elementwise over range_m
 * with no cross-element coupling, so replaying the identical scalar
 * arithmetic per call is the same floating-point operation sequence. */
static void candidate_trajectory(double launchRad, double rangeM,
                                  double speedMs, double teeXM,
                                  double launchHeightM, double radarHeightM,
                                  double lateralOffsetM, double *xMOut,
                                  double *heightMOut, double *directVrOut,
                                  double *imageVrOut) {
    double cosLaunch = cos(launchRad);
    double xM = d_clip(rangeM, 0.5, 6.0);
    int iter;
    double dx, timeS, heightM, vertical, modeledRange, dzDx, drDx;
    double vx, vz, horizontalSq, directVertical, imageVertical;
    double directRange, imageRange;

    for (iter = 0; iter < 8; iter++) {
        dx = xM - teeXM;
        timeS = dx / (speedMs * cosLaunch + 1e-9);
        heightM = launchHeightM + tan(launchRad) * dx - 0.5 * LCMF_GRAVITY_MS2 * timeS * timeS;
        vertical = heightM - radarHeightM;
        modeledRange = sqrt(xM * xM + lateralOffsetM * lateralOffsetM + vertical * vertical);
        dzDx = tan(launchRad) - LCMF_GRAVITY_MS2 * dx / ((speedMs * cosLaunch) * (speedMs * cosLaunch));
        drDx = (xM + vertical * dzDx) / fmax(modeledRange, 1e-9);
        xM = d_clip(xM - (modeledRange - rangeM) / drDx, 0.5, 6.0);
    }

    dx = xM - teeXM;
    timeS = dx / (speedMs * cosLaunch + 1e-9);
    heightM = launchHeightM + tan(launchRad) * dx - 0.5 * LCMF_GRAVITY_MS2 * timeS * timeS;
    vx = speedMs * cosLaunch;
    vz = speedMs * sin(launchRad) - LCMF_GRAVITY_MS2 * timeS;
    horizontalSq = xM * xM + lateralOffsetM * lateralOffsetM;
    directVertical = heightM - radarHeightM;
    imageVertical = heightM + radarHeightM;
    directRange = sqrt(horizontalSq + directVertical * directVertical);
    imageRange = sqrt(horizontalSq + imageVertical * imageVertical);

    *xMOut = xM;
    *heightMOut = heightM;
    *directVrOut = (xM * vx + directVertical * vz) / fmax(directRange, 1e-9);
    *imageVrOut = (xM * vx + imageVertical * vz) / fmax(imageRange, 1e-9);
}

/* Channel models this port covers -- lcmf.py's CHANNEL_MODELS is
 * ("two8", "four4_path_tdm"); "four4" (no TDM cross term) is only used by
 * the unported fast-time path, so it is intentionally absent here. */
typedef enum { LCMF_MODEL_TWO8 = 0, LCMF_MODEL_FOUR4_PATH_TDM = 1 } LcmfModel;

/* ---- _spatial_dictionary (lcmf.py:307), one snapshot at a time -------- */
static uint32_t spatial_dictionary(LcmfModel model, double launchRad, double rangeM,
                                    double speedMs, double teeXM, double ballHeightM,
                                    double radarHeightM, double tiltRad,
                                    uint8_t txOrderReversed, double tdmTauS,
                                    Cplx a[SOLVE_LCMF_N_ELEMENTS][SOLVE_LCMF_MAX_COEFFS]) {
    double xM, heightM, directVr, imageVr;
    double directAngle, imageAngle, sinDirect, sinImage;
    uint32_t e;
    static const double txHalfLambda[SOLVE_LCMF_N_ELEMENTS] = {
        0.0, 0.0, 0.0, 0.0, 4.0, 4.0, 4.0, 4.0,
    };
    static const double rxHalfLambda[SOLVE_LCMF_N_ELEMENTS] = {
        0.0, 1.0, 2.0, 3.0, 0.0, 1.0, 2.0, 3.0,
    };
    Cplx dd[SOLVE_LCMF_N_ELEMENTS];
    Cplx dg[SOLVE_LCMF_N_ELEMENTS];
    Cplx gd[SOLVE_LCMF_N_ELEMENTS];
    Cplx gg[SOLVE_LCMF_N_ELEMENTS];
    uint32_t nCols;

    candidate_trajectory(launchRad, rangeM, speedMs, teeXM, ballHeightM, radarHeightM,
                          LCMF_LATERAL_TEE_OFFSET_M, &xM, &heightM, &directVr, &imageVr);
    directAngle = atan2(heightM - radarHeightM, xM) - tiltRad;
    imageAngle = atan2(-(heightM + radarHeightM), xM) - tiltRad;
    sinDirect = sin(directAngle);
    sinImage = sin(imageAngle);

    for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
        double tx = txHalfLambda[e];
        double rx = rxHalfLambda[e];
        dd[e] = c_expi(LCMF_PI * (tx * sinDirect + rx * sinDirect));
        dg[e] = c_expi(LCMF_PI * (tx * sinDirect + rx * sinImage));
        gd[e] = c_expi(LCMF_PI * (tx * sinImage + rx * sinDirect));
        gg[e] = c_expi(LCMF_PI * (tx * sinImage + rx * sinImage));
    }

    if (model == LCMF_MODEL_FOUR4_PATH_TDM) {
        double crossPhase = 2.0 * LCMF_PI * (imageVr - directVr) * tdmTauS / LCMF_LAM_M;
        Cplx crossRot = c_expi(crossPhase);
        Cplx doubleRot = c_expi(2.0 * crossPhase);
        uint32_t laterTx = txOrderReversed ? 1U : 0U; /* later_physical_tx_index */
        uint32_t start = laterTx * SOLVE_LCMF_N_RX;
        uint32_t stop = start + SOLVE_LCMF_N_RX;
        for (e = start; e < stop; e++) {
            dg[e] = c_mul(dg[e], crossRot);
            gd[e] = c_mul(gd[e], crossRot);
            gg[e] = c_mul(gg[e], doubleRot);
        }
    }

    if (model == LCMF_MODEL_TWO8) {
        nCols = 2;
        for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
            a[e][0] = dd[e];
            a[e][1] = gg[e];
        }
    } else {
        nCols = 4;
        for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
            a[e][0] = dd[e];
            a[e][1] = dg[e];
            a[e][2] = gd[e];
            a[e][3] = gg[e];
        }
    }
    return nCols;
}

/* ---- complex kxk matrix inverse via Gauss-Jordan with partial pivoting
 * (k <= SOLVE_LCMF_MAX_COEFFS). Returns 0 on success, nonzero if singular
 * (matches numpy.linalg.LinAlgError's role -- not expected on this
 * corpus, see the task report's measured evidence). ------------------- */
static int cmat_inverse(Cplx a[SOLVE_LCMF_MAX_COEFFS][SOLVE_LCMF_MAX_COEFFS],
                         uint32_t k, Cplx inv[SOLVE_LCMF_MAX_COEFFS][SOLVE_LCMF_MAX_COEFFS]) {
    Cplx m[SOLVE_LCMF_MAX_COEFFS][2U * SOLVE_LCMF_MAX_COEFFS];
    uint32_t p, q, col, row, pivot;

    for (p = 0; p < k; p++) {
        for (q = 0; q < k; q++) {
            m[p][q] = a[p][q];
            m[p][k + q] = (p == q) ? c_make(1.0, 0.0) : c_make(0.0, 0.0);
        }
    }

    for (col = 0; col < k; col++) {
        double best = c_abs2(m[col][col]);
        pivot = col;
        for (row = col + 1; row < k; row++) {
            double mag = c_abs2(m[row][col]);
            if (mag > best) {
                best = mag;
                pivot = row;
            }
        }
        if (best < 1e-24) {
            return 1; /* singular */
        }
        if (pivot != col) {
            for (q = 0; q < 2U * k; q++) {
                Cplx tmp = m[col][q];
                m[col][q] = m[pivot][q];
                m[pivot][q] = tmp;
            }
        }
        {
            Cplx invPivot = c_div(c_make(1.0, 0.0), m[col][col]);
            for (q = 0; q < 2U * k; q++) {
                m[col][q] = c_mul(m[col][q], invPivot);
            }
        }
        for (row = 0; row < k; row++) {
            if (row == col) {
                continue;
            }
            Cplx factor = m[row][col];
            if (factor.re == 0.0 && factor.im == 0.0) {
                continue;
            }
            for (q = 0; q < 2U * k; q++) {
                m[row][q] = c_sub(m[row][q], c_mul(factor, m[col][q]));
            }
        }
    }

    for (p = 0; p < k; p++) {
        for (q = 0; q < k; q++) {
            inv[p][q] = m[p][k + q];
        }
    }
    return 0;
}

/* ---- multipath.leave_one_channel_out_error (multipath.py:173), one
 * snapshot at a time: pseudo = (A^H A)^-1 A^H via normal equations (A is
 * SOLVE_LCMF_N_ELEMENTS x k, full column rank by construction). -------- */
static int leave_one_channel_out_error(Cplx a[SOLVE_LCMF_N_ELEMENTS][SOLVE_LCMF_MAX_COEFFS],
                                        uint32_t k, const Cplx y[SOLVE_LCMF_N_ELEMENTS],
                                        double *errorOut) {
    Cplx gram[SOLVE_LCMF_MAX_COEFFS][SOLVE_LCMF_MAX_COEFFS];
    Cplx gramInv[SOLVE_LCMF_MAX_COEFFS][SOLVE_LCMF_MAX_COEFFS];
    Cplx pseudo[SOLVE_LCMF_MAX_COEFFS][SOLVE_LCMF_N_ELEMENTS]; /* k x N */
    Cplx coefficients[SOLVE_LCMF_MAX_COEFFS];
    Cplx prediction[SOLVE_LCMF_N_ELEMENTS];
    double leverage[SOLVE_LCMF_N_ELEMENTS];
    double press = 0.0;
    double power = 1e-12;
    uint32_t p, q, e;

    for (p = 0; p < k; p++) {
        for (q = 0; q < k; q++) {
            Cplx acc = c_make(0.0, 0.0);
            for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
                acc = c_add(acc, c_mul(c_conj(a[e][p]), a[e][q]));
            }
            gram[p][q] = acc;
        }
    }
    if (cmat_inverse(gram, k, gramInv) != 0) {
        return 1;
    }
    for (p = 0; p < k; p++) {
        for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
            Cplx acc = c_make(0.0, 0.0);
            for (q = 0; q < k; q++) {
                acc = c_add(acc, c_mul(gramInv[p][q], c_conj(a[e][q])));
            }
            pseudo[p][e] = acc;
        }
    }
    for (p = 0; p < k; p++) {
        Cplx acc = c_make(0.0, 0.0);
        for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
            acc = c_add(acc, c_mul(pseudo[p][e], y[e]));
        }
        coefficients[p] = acc;
    }
    for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
        Cplx acc = c_make(0.0, 0.0);
        Cplx lev = c_make(0.0, 0.0);
        for (p = 0; p < k; p++) {
            acc = c_add(acc, c_mul(a[e][p], coefficients[p]));
            lev = c_add(lev, c_mul(a[e][p], pseudo[p][e]));
        }
        prediction[e] = acc;
        leverage[e] = lev.re;
    }
    for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
        Cplx residual = c_sub(y[e], prediction[e]);
        double denom = fmax(1.0 - leverage[e], 1e-6);
        press += c_abs2(residual) / (denom * denom);
        power += c_abs2(y[e]);
    }
    *errorOut = press / power;
    return 0;
}

/* ---- small numeric helpers shared by _frame_objective/_refine_grid --- */
static double median_of(double *values, uint32_t n) {
    /* Insertion sort: n is at most SOLVE_LCMF_MAX_PER_FRAME (4). */
    uint32_t i, j;
    for (i = 1; i < n; i++) {
        double key = values[i];
        j = i;
        while (j > 0 && values[j - 1] > key) {
            values[j] = values[j - 1];
            j--;
        }
        values[j] = key;
    }
    if (n % 2U == 1U) {
        return values[n / 2U];
    }
    return 0.5 * (values[n / 2U - 1U] + values[n / 2U]);
}

/* ---- _frame_objective (lcmf.py:346) ------------------------------------
 * errors[i]/frameIds[i] for i in 0..n-1; frameIds need not be sorted or
 * deduplicated -- this groups exactly like np.unique(frames) does. */
static double frame_objective(const double *errors, const uint16_t *frameIds, uint32_t n,
                               double ceiling) {
    uint16_t seen[SOLVE_LCMF_MAX_FRAMES];
    uint32_t nSeen = 0;
    double logSum = 0.0;
    uint32_t i, s;

    for (i = 0; i < n; i++) {
        uint16_t f = frameIds[i];
        uint8_t already = 0;
        for (s = 0; s < nSeen; s++) {
            if (seen[s] == f) {
                already = 1;
                break;
            }
        }
        if (!already) {
            seen[nSeen++] = f;
        }
    }
    for (s = 0; s < nSeen; s++) {
        double bucket[SOLVE_LCMF_MAX_SELECTED];
        uint32_t count = 0;
        for (i = 0; i < n; i++) {
            if (frameIds[i] == seen[s]) {
                bucket[count++] = d_clip(errors[i], 1e-8, ceiling);
            }
        }
        logSum += log(median_of(bucket, count));
    }
    return logSum / (double)nSeen;
}

/* ---- _refine_grid (lcmf.py:353) ---------------------------------------- */
static double refine_grid(const double *gridDeg, const double *objective, uint32_t n) {
    uint32_t index = 0;
    double best = objective[0];
    uint32_t i;
    double y0, y1, y2, denom, offset, step;

    for (i = 1; i < n; i++) {
        if (objective[i] < best) {
            best = objective[i];
            index = i;
        }
    }
    if (!(index > 0 && index < n - 1U)) {
        return gridDeg[index];
    }
    y0 = objective[index - 1U];
    y1 = objective[index];
    y2 = objective[index + 1U];
    denom = y0 - 2.0 * y1 + y2;
    offset = (denom > 0.0) ? 0.5 * (y0 - y2) / denom : 0.0;
    step = gridDeg[1] - gridDeg[0];
    return gridDeg[index] + d_clip(offset, -1.0, 1.0) * step;
}

/* ---- grid_curvature (lcmf.py:363) -------------------------------------
 * hasEvidence=0 mirrors Python's "returns None" (edge argmin). */
static double grid_curvature(const double *objective, uint32_t n, uint8_t *hasEvidence) {
    uint32_t index = 0;
    double best = objective[0];
    uint32_t i;
    double y0, y1, y2, curvature;

    for (i = 1; i < n; i++) {
        if (objective[i] < best) {
            best = objective[i];
            index = i;
        }
    }
    if (!(index > 0 && index < n - 1U)) {
        *hasEvidence = 0;
        return 0.0;
    }
    y0 = objective[index - 1U];
    y1 = objective[index];
    y2 = objective[index + 1U];
    curvature = y0 - 2.0 * y1 + y2;
    if (curvature < 0.0) {
        curvature = 0.0;
    }
    *hasEvidence = 1;
    return curvature;
}

/* ---- Geometry.loop_time (tracking.py:91) ------------------------------- */
static double loop_time(const SolveLcmfLayout *layout, uint32_t frame, uint32_t loop) {
    uint32_t slotOrder = (frame + layout->nFrames - (layout->triggerFrame % layout->nFrames))
                         % layout->nFrames;
    double frameTime = (layout->frameTimeOffsetsS != NULL)
                            ? layout->frameTimeOffsetsS[slotOrder]
                            : (double)slotOrder * layout->framePeriodS;
    return frameTime + (double)loop * layout->loopPeriodS;
}

static void set_status(SolveLcmfResult *result, const char *status) {
    size_t len = strlen(status);
    if (len >= SOLVE_LCMF_STATUS_LEN) {
        len = SOLVE_LCMF_STATUS_LEN - 1U;
    }
    memcpy(result->status, status, len);
    result->status[len] = '\0';
}

/* ---- top-level entry point --------------------------------------------- */
uint32_t solve_lcmf_estimate(const SolveLcmfInput *in, SolveLcmfWorkspace *ws,
                              SolveLcmfResult *result) {
    const SolveLcmfLayout *layout = &in->layout;
    double gridDeg[SOLVE_LCMF_MAX_GRID];
    uint32_t nGrid;
    uint32_t i, f, loop;
    double speedMs, verticalDelta, teeXM;
    double objectiveTwo8[SOLVE_LCMF_MAX_GRID];
    double objectiveF4[SOLVE_LCMF_MAX_GRID];
    double estimateTwo8 = 0.0, estimateF4 = 0.0;
    uint8_t hasEvidenceTwo8 = 0, hasEvidenceF4 = 0;
    double evidenceTwo8 = 0.0, evidenceF4 = 0.0;
    uint32_t nUniqueFrames;
    uint16_t seenFrames[SOLVE_LCMF_MAX_FRAMES];

    memset(result, 0, sizeof(*result));
    result->angleDeg = NAN;
    result->rawAngleDeg = NAN;
    result->channelTwo8Deg = NAN;
    result->channelFour4PathTdmDeg = NAN;
    result->componentStdDeg = NAN;

    if (layout->nFrames == 0U || layout->nFrames > SOLVE_LCMF_MAX_FRAMES ||
        layout->nLoops == 0U || layout->nLoops > SOLVE_LCMF_MAX_LOOPS ||
        layout->nBins == 0U || layout->nBins > SOLVE_LCMF_MAX_BINS ||
        layout->nRx != SOLVE_LCMF_N_RX) {
        memset(result, 0, sizeof(*result));
        return SOLVE_LCMF_ERROR;
    }

    if (!in->trackFound) {
        set_status(result, "rejected_by_ball_tracker");
        return SOLVE_LCMF_OK;
    }
    if (in->qualityReject) {
        set_status(result, "rejected_track_quality");
        return SOLVE_LCMF_OK;
    }
    if (in->tdmSign != 1 && in->tdmSign != -1) {
        set_status(result, "rejected_missing_tdm_sign");
        return SOLVE_LCMF_OK;
    }

    /* ---- _snapshot_cache (lcmf.py:211) --------------------------------- */
    {
        double phaseVelocityMs = in->ballSpeedMph / LCMF_MPH_PER_MS;
        uint32_t nCache = 0;
        for (f = 0; f < layout->nFrames; f++) {
            for (loop = 0; loop < layout->nLoops; loop++) {
                double timeS = loop_time(layout, f, loop);
                double binAt, rangeAt, tdmPhase;
                int32_t rangeBin, localBin;
                Cplx early[SOLVE_LCMF_N_RX], late[SOLVE_LCMF_N_RX];
                Cplx uncalibrated[SOLVE_LCMF_N_ELEMENTS];
                double snrSum;
                uint32_t rx;

                if (!(in->track.tFirstS - 2e-3 <= timeS && timeS <= in->track.tLastS + 2e-3)) {
                    continue;
                }
                binAt = in->track.slopeBins * timeS + in->track.interceptBins;
                rangeBin = (int32_t)lround(binAt);
                localBin = rangeBin - layout->rangeBinStart;
                /* Geometry.contains_bin(margin=1): 1 <= local < nBins-1 */
                if (!(localBin >= 1 && localBin < (int32_t)layout->nBins - 1)) {
                    continue;
                }
                {
                    size_t base = (((size_t)f * 2U + 0U) * layout->nLoops + loop) *
                                  layout->nRx * layout->nBins;
                    size_t lateBase = (((size_t)f * 2U + 1U) * layout->nLoops + loop) *
                                      layout->nRx * layout->nBins;
                    for (rx = 0; rx < SOLVE_LCMF_N_RX; rx++) {
                        size_t idx = base + (size_t)rx * layout->nBins + (size_t)localBin;
                        size_t lidx = lateBase + (size_t)rx * layout->nBins + (size_t)localBin;
                        early[rx] = c_make(in->mtiRe[idx], in->mtiIm[idx]);
                        late[rx] = c_make(in->mtiRe[lidx], in->mtiIm[lidx]);
                    }
                }
                tdmPhase = (double)in->tdmSign * 4.0 * LCMF_PI * phaseVelocityMs * in->tdmTauS /
                           LCMF_LAM_M;
                canonicalize_tx_blocks(early, late, tdmPhase, in->txOrderReversed, uncalibrated);

                rangeAt = binAt * layout->rangeResM;

                snrSum = 0.0;
                for (rx = 0; rx < SOLVE_LCMF_N_ELEMENTS; rx++) {
                    snrSum += c_abs2(uncalibrated[rx]);
                }

                if (nCache >= SOLVE_LCMF_MAX_SNAPSHOTS) {
                    memset(result, 0, sizeof(*result));
                    return SOLVE_LCMF_ERROR;
                }
                ws->t[nCache] = timeS;
                ws->frame[nCache] = (uint16_t)f;
                ws->loopIdx[nCache] = (uint16_t)loop;
                ws->r[nCache] = rangeAt - in->cal.rangeBiasM; /* Calibration.true_range */
                ws->snr[nCache] = (snrSum / (double)SOLVE_LCMF_N_ELEMENTS) / in->noisePower;
                ws->vr[nCache] = phaseVelocityMs;
                for (rx = 0; rx < SOLVE_LCMF_N_ELEMENTS; rx++) {
                    /* Calibration.apply: elementwise multiply. */
                    Cplx calibrated = c_mul(uncalibrated[rx],
                                             c_make(in->cal.elemCorrRe[rx], in->cal.elemCorrIm[rx]));
                    ws->vecRe[nCache][rx] = calibrated.re;
                    ws->vecIm[nCache][rx] = calibrated.im;
                }
                nCache++;
            }
        }
        ws->nCache = nCache;
    }

    /* ---- _balanced_indices (lcmf.py:280) -------------------------------- */
    {
        uint32_t nSelected = 0;
        for (f = 0; f < layout->nFrames; f++) {
            uint16_t frameIdx[SOLVE_LCMF_MAX_LOOPS];
            double frameSnr[SOLVE_LCMF_MAX_LOOPS];
            uint32_t count = 0;
            uint32_t take, k;
            for (i = 0; i < ws->nCache; i++) {
                if (ws->frame[i] == (uint16_t)f && ws->snr[i] >= LCMF_MIN_SNR &&
                    ws->r[i] <= LCMF_MAX_RANGE_M) {
                    frameIdx[count] = (uint16_t)i;
                    frameSnr[count] = ws->snr[i];
                    count++;
                }
            }
            /* argsort descending by SNR, insertion sort (count is small). */
            for (i = 1; i < count; i++) {
                uint16_t keyIdx = frameIdx[i];
                double keySnr = frameSnr[i];
                uint32_t j = i;
                while (j > 0 && frameSnr[j - 1] < keySnr) {
                    frameSnr[j] = frameSnr[j - 1];
                    frameIdx[j] = frameIdx[j - 1];
                    j--;
                }
                frameSnr[j] = keySnr;
                frameIdx[j] = keyIdx;
            }
            take = (count < SOLVE_LCMF_MAX_PER_FRAME) ? count : SOLVE_LCMF_MAX_PER_FRAME;
            for (k = 0; k < take; k++) {
                ws->selected[nSelected++] = frameIdx[k];
            }
        }
        ws->nSelected = nSelected;
    }

    /* unique frame count among selected */
    nUniqueFrames = 0;
    for (i = 0; i < ws->nSelected; i++) {
        uint16_t fr = ws->frame[ws->selected[i]];
        uint8_t already = 0;
        uint32_t s;
        for (s = 0; s < nUniqueFrames; s++) {
            if (seenFrames[s] == fr) {
                already = 1;
                break;
            }
        }
        if (!already) {
            seenFrames[nUniqueFrames++] = fr;
        }
    }

    if (ws->nSelected < 12U || nUniqueFrames < 3U) {
        set_status(result, "insufficient_channel_snapshots");
        return SOLVE_LCMF_OK;
    }

    /* ---- grid_deg = np.arange(-5, 45+step/2, step) ---------------------- */
    {
        double stop = LCMF_GRID_HI_DEG + in->gridStepDeg / 2.0;
        double count = ceil((stop - LCMF_GRID_LO_DEG) / in->gridStepDeg - 1e-9);
        if (!(count >= 1.0) || count > (double)SOLVE_LCMF_MAX_GRID) {
            memset(result, 0, sizeof(*result));
            return SOLVE_LCMF_ERROR;
        }
        nGrid = (uint32_t)count;
        for (i = 0; i < nGrid; i++) {
            gridDeg[i] = LCMF_GRID_LO_DEG + (double)i * in->gridStepDeg;
        }
    }

    speedMs = in->ballSpeedMph / LCMF_MPH_PER_MS;
    verticalDelta = in->cal.teeBallHeightM - in->cal.radarHeightM;
    teeXM = sqrt(fmax(in->cal.teeRangeM * in->cal.teeRangeM - verticalDelta * verticalDelta, 0.25));

    /* ---- _channel_estimates (lcmf.py:442), models two8/four4_path_tdm --- */
    {
        double rangeM[SOLVE_LCMF_MAX_SELECTED];
        uint16_t frameIds[SOLVE_LCMF_MAX_SELECTED];
        double errors[SOLVE_LCMF_MAX_SELECTED];
        uint32_t modelIdx;

        for (i = 0; i < ws->nSelected; i++) {
            uint16_t idx = ws->selected[i];
            rangeM[i] = ws->r[idx];
            frameIds[i] = ws->frame[idx];
        }

        for (modelIdx = 0; modelIdx < 2U; modelIdx++) {
            LcmfModel model = (LcmfModel)modelIdx;
            double *objective = (model == LCMF_MODEL_TWO8) ? objectiveTwo8 : objectiveF4;
            uint32_t g;
            for (g = 0; g < nGrid; g++) {
                double launchRad = gridDeg[g] * LCMF_PI / 180.0;
                for (i = 0; i < ws->nSelected; i++) {
                    uint16_t idx = ws->selected[i];
                    Cplx a[SOLVE_LCMF_N_ELEMENTS][SOLVE_LCMF_MAX_COEFFS];
                    Cplx y[SOLVE_LCMF_N_ELEMENTS];
                    uint32_t k, e;
                    k = spatial_dictionary(model, launchRad, rangeM[i], speedMs, teeXM,
                                           in->cal.teeBallHeightM, in->cal.radarHeightM,
                                           in->cal.tiltRad, in->txOrderReversed, in->tdmTauS, a);
                    for (e = 0; e < SOLVE_LCMF_N_ELEMENTS; e++) {
                        y[e] = c_make(ws->vecRe[idx][e], ws->vecIm[idx][e]);
                    }
                    if (leave_one_channel_out_error(a, k, y, &errors[i]) != 0) {
                        errors[i] = 1e3; /* singular dictionary: clip ceiling, matches
                                           * lcmf.py's np.clip(...,1e-8,1e3) upper bound
                                           * rather than propagating a LinAlgError --
                                           * not exercised by this corpus (see report). */
                    }
                }
                objective[g] = frame_objective(errors, frameIds, ws->nSelected, 1e3);
            }
        }
        estimateTwo8 = refine_grid(gridDeg, objectiveTwo8, nGrid);
        evidenceTwo8 = grid_curvature(objectiveTwo8, nGrid, &hasEvidenceTwo8);
        estimateF4 = refine_grid(gridDeg, objectiveF4, nGrid);
        evidenceF4 = grid_curvature(objectiveF4, nGrid, &hasEvidenceF4);
    }

    result->channelTwo8Deg = estimateTwo8;
    result->channelFour4PathTdmDeg = estimateF4;
    result->hasChannelTwo8 = hasEvidenceTwo8;
    result->hasChannelFour4PathTdm = hasEvidenceF4;

    /* ---- measured_channels / combine_channels (lcmf.py:390/407) -------- */
    {
        double rawAngle;
        uint32_t nMeasured = (hasEvidenceTwo8 ? 1U : 0U) + (hasEvidenceF4 ? 1U : 0U);
        uint8_t singleChannel = 0;
        uint32_t nUsed = 0;
        char used[2][32];

        if (nMeasured == 0U) {
            set_status(result, "rejected_no_conditioned_channel");
            return SOLVE_LCMF_OK;
        }
        if (nMeasured == 1U) {
            rawAngle = hasEvidenceTwo8 ? estimateTwo8 : estimateF4;
            singleChannel = 1;
            nUsed = 1;
            strcpy(used[0], hasEvidenceTwo8 ? "channel_two8_deg" : "channel_four4_path_tdm_deg");
        } else if (fabs(estimateTwo8 - estimateF4) <= LCMF_CHANNEL_SPREAD_MAX_DEG) {
            rawAngle = 0.5 * (estimateTwo8 + estimateF4);
            singleChannel = 0;
            nUsed = 2;
            strcpy(used[0], "channel_two8_deg");
            strcpy(used[1], "channel_four4_path_tdm_deg");
        } else {
            /* Both measured implies both evidence > 0.0 (grid_curvature's
             * documented invariant: an interior argmin's curvature is
             * always strictly positive) -- so both are "conditioned" and
             * combine_channels' no-conditioned-channel sub-branch cannot
             * fire here. Confirmed against all 18 corpus cases. */
            singleChannel = 1;
            nUsed = 1;
            if (evidenceTwo8 >= evidenceF4) {
                rawAngle = estimateTwo8;
                strcpy(used[0], "channel_two8_deg");
            } else {
                rawAngle = estimateF4;
                strcpy(used[0], "channel_four4_path_tdm_deg");
            }
        }

        {
            double mean = 0.0, variance = 0.0;
            if (nMeasured == 1U) {
                mean = hasEvidenceTwo8 ? estimateTwo8 : estimateF4;
            } else {
                mean = 0.5 * (estimateTwo8 + estimateF4);
                variance = 0.5 * ((estimateTwo8 - mean) * (estimateTwo8 - mean) +
                                   (estimateF4 - mean) * (estimateF4 - mean));
            }
            result->componentStdDeg = sqrt(variance);
        }

        set_status(result, "accepted");
        result->rawAngleDeg = rawAngle;
        result->angleDeg = rawAngle + LCMF_ANGLE_CORRECTION_DEG;
        result->singleChannel = singleChannel;
        result->nChannelsUsed = nUsed;
        for (i = 0; i < nUsed; i++) {
            strcpy(result->channelsUsed[i], used[i]);
        }
        result->nSnapshots = ws->nSelected;
        result->nFrames = nUniqueFrames;
    }

    return SOLVE_LCMF_OK;
}
