/* Tracking stage: ball detection + RANSAC range-walk fit for the DSS solve.
 *
 * Port of src/openflight/iwr6843/tracking.py's find_ball()/find_ball_from_power()
 * (via the thin loop_power() wrapper -- see the module docstring there) at
 * the "ball" default gates/speed bounds. Python stays the reference; this
 * header documents where the implementation reuses, extends, or diverges
 * from firmware/iwr6843/track_select.c -- see solve_tracking.c's file
 * banner for the full audit written for Task 5 of the on-chip solve plan.
 *
 * Pure C99, no TI headers, struct-in/struct-out, no globals, no malloc.
 */
#ifndef L3_SOLVE_TRACKING_H
#define L3_SOLVE_TRACKING_H

#include <stdint.h>

/* nFrames/nLoops mirror track_select.h's L3T_MAX_FRAMES/L3T_MAX_LOOPS,
 * which mirror l3_dump.c's L3_MAX_CAPTURE_FRAMES/L3_MAX_LOOPS -- the real
 * L3 ring-buffer capture arena limits, the SAME arena solve_ipc.h's
 * L3SolveRequest reads. nBins is deliberately NOT track_select.h's
 * L3T_MAX_BINS (64): that value is sized for the l3track CLI's narrower
 * debug ring window (a different host planner, sparse.py). The solve
 * stage's shot captures run the full range FFT, which is 128 samples on
 * every chirp profile this project configures -- tracking.py:365's own
 * "geo.n_samples >= 128" branch condition, and solve_fft.h's
 * SOLVE_FFT_MAX_SAMPLES -- so 128 is used here instead. nRx follows
 * solve_fft.h's own precedent: 4 on every configured geometry. */
#define SOLVE_TRACKING_MAX_FRAMES     64U
#define SOLVE_TRACKING_MAX_LOOPS      16U
#define SOLVE_TRACKING_MAX_BINS       128U
#define SOLVE_TRACKING_MAX_RX         4U
#define SOLVE_TRACKING_MAX_GATES      2U   /* tracking.BALL_GATES_M */
#define SOLVE_TRACKING_MAX_ROWS       (SOLVE_TRACKING_MAX_FRAMES * SOLVE_TRACKING_MAX_LOOPS)
#define SOLVE_TRACKING_MAX_DETECTIONS (SOLVE_TRACKING_MAX_ROWS * SOLVE_TRACKING_MAX_GATES)

#define SOLVE_TRACKING_OK    0U
#define SOLVE_TRACKING_ERROR 1U

typedef struct {
    double loM;
    double hiM;
} SolveTrackingGate;

/* Capture layout, rebuilt from the dump header -- mirrors tracking.Geometry
 * fields find_ball()/find_ball_from_power() actually read. */
typedef struct {
    uint32_t       nFrames;
    uint32_t       nLoops;        /* geo.n_loops = chirps_per_frame // n_tx */
    uint32_t       nBins;         /* geo.n_samples: widest per-frame bin count */
    uint32_t       nRx;           /* geo.n_rx: RX channels summed by loop_power */
    uint32_t       triggerFrame;  /* geo.trigger_frame */
    double         framePeriodS;
    double         loopPeriodS;
    double         rangeResM;
    uint32_t       rangeBinStart; /* geo.range_bin_start; used only when binStarts is NULL */
    /* Windowed-range-dump support (geo.range_bin_start{s,counts} not None).
     * NULL for both means every frame starts at rangeBinStart and stores
     * nBins bins -- the uniform layout every Task-5 golden case exercises. */
    const uint32_t *binStarts;    /* absolute first bin per frame, length nFrames */
    const uint32_t *binCounts;    /* stored bin count per frame, length nFrames */
    /* geo.frame_time_offsets_s: NULL means slot_order * framePeriodS (the
     * fixed-period case); non-NULL supplies per-slot offsets directly. */
    const double   *frameTimeOffsetsS;
} SolveTrackingLayout;

typedef struct {
    SolveTrackingGate gates[SOLVE_TRACKING_MAX_GATES];
    uint32_t          nGates;
    double            maxRangeM;    /* <= 0 disables the clamp (max_range_m=None) */
    double            snrMin;       /* tracking._detections snr_min, default 4.0 */
    double            speedMinMs;   /* tracking.SPEED_BOUNDS_MS */
    double            speedMaxMs;
    double            minBallMs;    /* find_ball's min_ball_ms, default FAST_TRACK_MS */
    double            fastSupportFrac; /* tracking.FAST_SUPPORT_FRAC */
    double            minPairDtS;   /* find_ball_from_power's "abs(d_t) < 3e-3" */
    uint32_t          iterations;   /* find_ball's iterations, default 2500 */
    uint32_t          minDetections; /* find_ball_from_power's literal 8 */
} SolveTrackingParams;

/* Fills *params with tracking.py's module-level defaults for find_ball()'s
 * ball-gate search. Mirrors track_select.c's l3track_default_params(). */
void solve_tracking_default_params(SolveTrackingParams *params);

typedef struct {
    uint8_t  found;
    uint32_t nInliers;
    double   speedMs;
    double   slopeBins;
    double   interceptBins;
    double   rmsBins;
    double   tFirstS;
    double   tLastS;
    uint8_t  lowConfidence;  /* rms_bins >= 0.45 or (t_last - t_first) < 0.012 */
} SolveTrackingResult;

/* Fills out[0..count) with tracking.loop_power's per-row reduction for one
 * (frame, loop): MTI residual power per bin. The caller supplies the MTI
 * cube's raw re/im samples for that row across both TX blocks and every RX
 * channel via mtiRowFn below; this stage does the |.|^2 sum itself -- see
 * solve_tracking.c's solve_tracking_loop_power(). */
typedef struct {
    /* Row layout: [tx (2)][rx][bin], tx-major, flattened. rx runs 0..nRx-1,
     * bin runs 0..count-1. Caller fills exactly nTx*nRx*count entries. */
    double re[2U * SOLVE_TRACKING_MAX_RX * SOLVE_TRACKING_MAX_BINS];
    double im[2U * SOLVE_TRACKING_MAX_RX * SOLVE_TRACKING_MAX_BINS];
} SolveTrackingMtiRow;

typedef void (*SolveTrackingMtiRowFn)(void *ctx, uint32_t frame, uint32_t loop,
                                      uint32_t nRx, uint32_t count,
                                      SolveTrackingMtiRow *row);

/* Draw two distinct indices below n. Identical signature to
 * track_select.h's L3TrackPairFn -- l3track_rng_pair (track_select.c) can be
 * passed directly; this stage reuses it rather than inventing a second RNG,
 * per the plan's explicit instruction. */
typedef void (*SolveTrackingPairFn)(void *ctx, uint32_t n, uint32_t *i, uint32_t *j);

/* Caller-owned scratch. At SOLVE_TRACKING_MAX_* sizes this is several tens
 * of KB -- far too large for a stack local -- so, like
 * track_select.h's L3TrackWorkspace, it is always passed by pointer and the
 * caller decides where it lives (a file-scope static in the DSS build, a
 * ctypes Structure in the host tests). solve_tracking.c itself declares no
 * globals. */
typedef struct {
    uint16_t detRow[SOLVE_TRACKING_MAX_DETECTIONS];  /* gate g owns [g*MAX_ROWS, ...) */
    double   detBin[SOLVE_TRACKING_MAX_DETECTIONS];
    uint32_t detCount[SOLVE_TRACKING_MAX_GATES];
    uint16_t order[SOLVE_TRACKING_MAX_DETECTIONS];   /* host detection order */
    uint32_t nOrder;
    double   row[SOLVE_TRACKING_MAX_BINS];           /* one row's power */
    double   scratch[SOLVE_TRACKING_MAX_BINS];       /* median sort scratch */
    SolveTrackingMtiRow mtiRow;                      /* one row's raw MTI samples */
} SolveTrackingWorkspace;

/* Runs tracking.find_ball()'s RANSAC range-walk search: scans every
 * (frame, loop) row via mtiRowFn/loop_power, gates detections into
 * params->gates, then RANSACs a fastest-credible range walk over
 * params->iterations draws from pairFn.
 *
 * Returns SOLVE_TRACKING_OK once the search ran to completion -- result->found
 * distinguishes "ball found" from "no plausible streak" (find_ball()
 * returning None), which is a normal, expected outcome, not an error.
 * Returns SOLVE_TRACKING_ERROR only when layout/params exceed the fixed
 * limits above; *result is zeroed in that case (no partial write). */
uint32_t solve_tracking_find_ball(const SolveTrackingLayout *layout,
                                   const SolveTrackingParams *params,
                                   SolveTrackingMtiRowFn mtiRowFn, void *mtiRowCtx,
                                   SolveTrackingPairFn pairFn, void *pairCtx,
                                   SolveTrackingWorkspace *ws,
                                   SolveTrackingResult *result);

#endif /* L3_SOLVE_TRACKING_H */
