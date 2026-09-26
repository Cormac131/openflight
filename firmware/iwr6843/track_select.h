/* On-chip ball track and cell selection for the l3track command.
 *
 * Port of the host planner: tracking._detections, find_ball_from_power
 * (without the quadratic refit, which cell selection never reads),
 * sparse.track_cells and the club-gate peaks in
 * IWR6843Runtime.plan_sparse_cells. Python stays the reference.
 * tests/test_iwr6843_track_select.py compiles this file with the host C
 * compiler and checks it names the same cells as the Python planner.
 *
 * Pure C99 with no TI headers, so the host build and the R4F build share
 * one source.
 */
#ifndef L3_TRACK_SELECT_H
#define L3_TRACK_SELECT_H

#include <stdint.h>

#define L3T_MAX_FRAMES      64U   /* L3_MAX_CAPTURE_FRAMES */
#define L3T_MAX_LOOPS       16U   /* L3_MAX_LOOPS */
#define L3T_MAX_BINS        64U   /* L3_RING_MAX_BINS, one uint64 mask per frame */
#define L3T_MAX_BALL_GATES  2U
#define L3T_MAX_ROWS        (L3T_MAX_FRAMES * L3T_MAX_LOOPS)
#define L3T_MAX_DETECTIONS  (L3T_MAX_ROWS * L3T_MAX_BALL_GATES)

typedef struct {
    double loM;
    double hiM;
} L3TrackGate;

/* Capture layout, as the host rebuilds it from the ILP1/ILT1 header. */
typedef struct {
    uint32_t       nFrames;
    uint32_t       nLoops;
    uint32_t       maxBins;       /* widest frame; header n_samples */
    const uint8_t *binStarts;     /* absolute first bin per frame */
    const uint8_t *binCounts;     /* stored bins per frame */
    double         framePeriodS;
    double         loopPeriodS;
    double         rangeResM;
} L3TrackLayout;

typedef struct {
    L3TrackGate ballGates[L3T_MAX_BALL_GATES];
    uint32_t    nBallGates;
    double      maxRangeM;        /* clamps the ball gates; <= 0 disables */
    L3TrackGate clubGate;         /* hiM <= loM disables */
    double      snrMin;
    double      speedMinMs;
    double      speedMaxMs;
    double      fastTrackMs;
    double      fastSupportFrac;
    double      minPairDtS;
    double      cellPadS;
    uint32_t    iterations;
    uint32_t    minDetections;
    uint32_t    cellMargin;
    uint32_t    clubMargin;
} L3TrackParams;

typedef struct {
    uint8_t  found;
    uint32_t nInliers;
    double   slopeBins;           /* bins per second */
    double   interceptBins;
    double   rmsBins;
    double   tFirstS;
    double   tLastS;
} L3TrackResult;

/* Fill out[0..count) with residual power for (frame, loop). */
typedef void (*L3TrackRowFn)(void *ctx, uint32_t frame, uint32_t loop,
                             float *out, uint32_t count);
/* Draw two distinct indices below n. */
typedef void (*L3TrackPairFn)(void *ctx, uint32_t n, uint32_t *i, uint32_t *j);

typedef struct {
    uint16_t detRow[L3T_MAX_DETECTIONS];   /* gate g owns [g*MAX_ROWS, ...) */
    float    detBin[L3T_MAX_DETECTIONS];
    uint32_t detCount[L3T_MAX_BALL_GATES];
    uint16_t order[L3T_MAX_DETECTIONS];    /* host detection order */
    uint32_t nOrder;
    float    row[L3T_MAX_BINS];
    float    scratch[L3T_MAX_BINS];
    uint64_t cellMask[L3T_MAX_FRAMES];     /* bit b: local bin b requested */
} L3TrackWorkspace;

typedef struct {
    uint32_t state;
} L3TrackRng;

void l3track_default_params(L3TrackParams *params);

/* Inlier tolerance in bins; mirrors find_ball_from_power's n_samples rule. */
double l3track_inlier_tol(uint32_t maxBins);

/* Round half to even, as Python's round(). */
double l3track_round(double value);

/* Select cells into ws->cellMask. Returns the cell count, or -1 when the
 * layout exceeds the fixed limits above. */
int32_t l3track_select(const L3TrackLayout *layout,
                       const L3TrackParams *params,
                       L3TrackRowFn rowFn, void *rowCtx,
                       L3TrackPairFn pairFn, void *pairCtx,
                       L3TrackWorkspace *ws,
                       L3TrackResult *result);

/* Deterministic xorshift32 pair source for the firmware. */
void l3track_rng_seed(L3TrackRng *rng, uint32_t seed);
void l3track_rng_pair(void *ctx, uint32_t n, uint32_t *i, uint32_t *j);

#endif /* L3_TRACK_SELECT_H */
