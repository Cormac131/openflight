/* IWR6843 ball hypotheses: the ball found after impact, not assumed.
 *
 * After the gate fires, the club carries on through impact and is usually
 * the strongest, most confident departing return. Taking the most confident
 * target in the departure band therefore follows the club. Instead, up to
 * L3_BALL_HYP_MAX candidate trajectories are kept that start near the origin;
 * each frame's targets are assigned to them jointly with the club track, whose
 * claimed target never becomes a ball point (a merged return is a missed
 * frame); and only once a hypothesis holds enough points is it judged
 * (l3_ball_hyps_classify): origin crossing near the gate time, a physical
 * range rate, a straight fit, Doppler agreeing with the rate, and a return
 * weaker than the club's. Prediction, gates and fits run on timestamps, not
 * frame counts. Pure C, fixed size, no hardware.
 */
#ifndef L3_BALL_HYP_H
#define L3_BALL_HYP_H

#include <stdint.h>

#include "l3_observation.h"

#define L3_BALL_HYP_MAX    4U
#define L3_BALL_HYP_POINTS 8U
#define L3_BALL_HYP_NONE   0xFFFFFFFFU

typedef struct {
    uint32_t frame;
    uint32_t timestampUs;
    float    rangeBin;            /* global, sub-bin */
    float    dopplerAliasMps;
    float    stat;
    float    clubStat;            /* the club's claimed return that frame, 0 without one */
    float    azimuthRad;
    float    elevationRad;
    uint8_t  anglesValid;         /* L3_OBS_ANGLE_* bits */
} l3_ball_hyp_point_t;

typedef struct {
    uint8_t  active;
    uint8_t  count;
    uint8_t  misses;              /* consecutive frames without a point */
    uint32_t id;                  /* spawn order: the tie-break */
    uint32_t lastTargetIndex;     /* this frame's target, L3_BALL_HYP_NONE when none */
    l3_ball_hyp_point_t points[L3_BALL_HYP_POINTS];  /* oldest first; the oldest slides out */
} l3_ball_hyp_t;

typedef struct {
    float    binWidthM;           /* l3_ball_track_init copies these two from its core */
    float    velocitySpanMps;
    float    spawnBehindBins;     /* a hypothesis starts from origin - this ... */
    float    spawnBeyondBins;     /* ... to origin + this, + maxSpeedMps x the time
                                   * since the gate (a late gate finds the ball out) */
    float    gateBins;            /* association half-width at zero elapsed time ... */
    float    gateMps;             /* ... growing by this speed uncertainty over the gap */
    uint32_t maxMisses;           /* coasted frames before a hypothesis is dropped */
    uint32_t classifyPoints;      /* points before a hypothesis may be the ball */
    float    minDepartureMps;
    float    maxSpeedMps;
    uint32_t impactToleranceUs;   /* the fit must reach the origin this close to the gate time */
    float    maxResidualBins;     /* RMS about the fitted line */
    float    dopplerToleranceMps; /* a point agrees when its Doppler is this close to the rate */
} l3_ball_hyps_cfg_t;

typedef struct {
    int32_t  index;               /* hypothesis index, -1 when none qualifies */
    uint32_t points;
    float    rateMps;             /* fitted range rate */
    float    originOffsetUs;      /* origin crossing minus the gate time */
    float    residualBins;
    float    dopplerAgreement;    /* 0..1 */
    float    weakerFraction;      /* 0..1 of the frames with a club return; 0.5 without */
    float    score;
} l3_ball_hyp_verdict_t;

typedef struct {
    l3_ball_hyps_cfg_t cfg;
    uint8_t  armed;
    float    originBin;
    uint32_t impactTimestampUs;   /* the gate time the tracker was armed at */
    uint32_t nextId;
    uint32_t spawned;
    uint32_t dropped;             /* coasted out or evicted */
    l3_ball_hyp_t hyp[L3_BALL_HYP_MAX];
} l3_ball_hyps_t;

void l3_ball_hyps_cfg_defaults(l3_ball_hyps_cfg_t *cfg);
void l3_ball_hyps_init(l3_ball_hyps_t *hyps, const l3_ball_hyps_cfg_t *cfg);
/* Forget every hypothesis and start looking from originBin at the gate time. */
void l3_ball_hyps_arm(l3_ball_hyps_t *hyps, float originBin, uint32_t impactTimestampUs);
/* One post-impact frame's targets (strongest first) and the index of the one
 * the club track claimed (L3_TRACK_NO_TARGET, or anything >= n, for none).
 * Returns the hypotheses active afterwards. */
uint32_t l3_ball_hyps_update(l3_ball_hyps_t *hyps, const l3_target_obs_t *targets, uint32_t n,
                             uint32_t frame, uint32_t timestampUs, uint32_t clubIndex);
/* Least squares of range against time over the hypothesis's points, time
 * measured from referenceUs: rate in bins per second, the fitted range at the
 * reference time and the RMS residual. Returns 0 with fewer than 2 points or
 * no spread in time. */
int32_t l3_ball_hyp_fit(const l3_ball_hyp_t *hyp, uint32_t referenceUs, float *rateBinsPerS,
                        float *binAtReference, float *residualBins);
/* Angles for the point hypothesis `index` appended this frame. Returns 0 when
 * it appended nothing this frame or the index is out of range. */
int32_t l3_ball_hyps_set_angles(l3_ball_hyps_t *hyps, uint32_t index, float azimuthRad,
                                float elevationRad, uint8_t anglesValid);
/* The ball among the hypotheses holding at least classifyPoints points:
 * fitted over them from the gate time, it must move outward at
 * minDepartureMps..maxSpeedMps, cross the origin within impactToleranceUs of
 * the gate time and fit within maxResidualBins. The best score wins:
 * (1 - residual / maxResidualBins) + the fraction of points whose Doppler
 * agrees with the rate + half the fraction of club frames where it was the
 * weaker return. out->index is -1 when none qualifies. */
void l3_ball_hyps_classify(const l3_ball_hyps_t *hyps, l3_ball_hyp_verdict_t *out);
/* sizeof(l3_ball_hyps_t), for the ctypes mirror's layout check. */
uint32_t l3_ball_hyps_struct_bytes(void);

#endif /* L3_BALL_HYP_H */
