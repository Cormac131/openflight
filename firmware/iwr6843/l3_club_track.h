/* IWR6843 club track: a persistent trajectory history with predictive
 * association, over the observation layer's targets.
 *
 * The trigger's trackBin/trackAge is enough to fire on; it is not a
 * measurement. This keeps the last L3_TRACK_POINTS observations of the
 * clubhead as a circular history, associates each frame's targets to the
 * track by predicting where the club should be (range, then Doppler
 * continuity and signal quality) instead of taking the strongest return, so
 * the shaft, hands and body cannot steal the track, and fits range against
 * time over the recent points for a club speed that does not depend on the
 * aliased Doppler. Angles are carried through for the 3D trajectory to come.
 *
 * Bins are GLOBAL range-FFT bins. Toward the ball means a rising bin. Pure C.
 */
#ifndef L3_CLUB_TRACK_H
#define L3_CLUB_TRACK_H

#include <stdint.h>

#include "l3_observation.h"

#define L3_TRACK_POINTS 32U

typedef struct {
    uint32_t frame;
    uint32_t timestampUs;
    float    rangeBin;            /* global, sub-bin */
    float    rangeM;
    float    radialVelocityMps;   /* from the range rate, not Doppler */
    float    dopplerAliasMps;
    float    azimuthRad;
    float    elevationRad;
    uint8_t  anglesValid;
    float    energy;
    float    coherence;
    float    confidence;
} l3_track_point_t;

typedef struct {
    float    binWidthM;           /* range per bin */
    float    gateBins;            /* association gate around the prediction */
    uint32_t maxMisses;           /* frames coasted on the prediction */
    float    minConfidence;       /* acquire only a target this confident */
    float    weightRange;         /* score = wR * rangeErrBins + ... */
    float    weightVelocity;      /*       + wV * wrapped Doppler diff / span */
    float    weightQuality;       /*       + wQ * (1 - confidence) */
    float    velocitySpanMps;     /* Doppler alias span (2 * wavelength / 4T) */
} l3_track_cfg_t;

enum {
    L3_TRACK_WHY_NONE = 0,
    L3_TRACK_WHY_ACQUIRED,
    L3_TRACK_WHY_ASSOCIATED,
    L3_TRACK_WHY_COASTED,       /* nothing in the gate; predicted forward */
    L3_TRACK_WHY_DROPPED,       /* coasted too long */
    L3_TRACK_WHY_IDLE,          /* no track and nothing confident enough */
    L3_TRACK_WHY_COUNT
};

typedef struct {
    l3_track_cfg_t cfg;
    uint8_t  active;
    uint8_t  why;                 /* last update */
    uint32_t next;                /* ring write index */
    uint32_t count;               /* points held, at most L3_TRACK_POINTS */
    uint32_t total;               /* points appended since init */
    uint32_t misses;
    uint32_t lastFrame;
    float    lastBin;
    float    velocityBinsPerFrame;
    float    predictedBin;
    l3_track_point_t points[L3_TRACK_POINTS];
    uint32_t counters[L3_TRACK_WHY_COUNT];
} l3_club_track_t;

void l3_track_cfg_defaults(l3_track_cfg_t *cfg);
void l3_track_init(l3_club_track_t *track, const l3_track_cfg_t *cfg);
/* Forget the track and its history; keep the configuration and counters. */
void l3_track_reset(l3_club_track_t *track);
/* One frame's targets (strongest first, from l3_obs_extract). Returns 1
 * when a point was appended. Frames without a call are frames without
 * observations; the prediction uses frame numbers, so call once per frame. */
int32_t l3_track_update(l3_club_track_t *track, const l3_target_obs_t *targets, uint32_t n,
                        uint32_t frame, uint32_t timestampUs);
/* Point index 0 is the oldest held. Returns 0 when out of range. */
int32_t l3_track_point(const l3_club_track_t *track, uint32_t index, l3_track_point_t *out);
/* Least-squares fit of rangeBin against time over the newest maxPoints
 * points (at least 3). Returns the points used, 0 when too few; slope in
 * bins per second, residual as RMS bins. */
uint32_t l3_track_fit(const l3_club_track_t *track, uint32_t maxPoints, float *slopeBinsPerS,
                      float *residualBins);
/* |fitted slope| in m/s over the newest maxPoints, 0 without a fit. */
float l3_track_speed_mps(const l3_club_track_t *track, uint32_t maxPoints);
const char *l3_track_why_name(uint8_t why);
/* "clubtrack active=1 count=14 bin=44.20 dist=3.8 vel=1.92 speed=22.4 ..." */
int32_t l3_track_format_status(const l3_club_track_t *track, uint32_t destBin, char *out,
                               uint32_t cap);
int32_t l3_track_format_point(const l3_track_point_t *point, uint32_t destBin, char *out,
                              uint32_t cap);

#endif /* L3_CLUB_TRACK_H */
