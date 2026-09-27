/* IWR6843 ball track: the departing ball after impact.
 *
 * Kept apart from the club track even though both share the trajectory core
 * (l3_club_track.c: ring, predictive association, range-over-time fit),
 * because what is looked for differs. Before impact the target is the
 * approaching club; after it, a coherent return leaving the ball's origin
 * fast: acquired only at or beyond the origin bin, within a short gate, and
 * confirmed by the range rate its second point shows. Until then only
 * candidates at least a bin BEYOND the reference (the origin, then the first
 * point) are offered, which excludes the impact echo, the resting club and
 * the clubhead's follow-through behind the ball; once flying, the ball never
 * comes back toward the radar, so a candidate behind the last point is never
 * it. A return that fails the departure test is dropped and the search
 * restarts.
 *
 * The launch is fitted over the EARLIEST clean points of the flight, not
 * the newest, because drag takes speed off from the first metre: the
 * velocity vector extrapolated to the impact time gives ball speed, the
 * horizontal launch angle (positive right) and the vertical launch angle
 * (positive up) in the golf frame. Pure C, no hardware.
 */
#ifndef L3_BALL_TRACK_H
#define L3_BALL_TRACK_H

#include <stdint.h>

#include "l3_club_track.h"
#include "l3_frames.h"

typedef struct {
    l3_track_cfg_t core;          /* association gate, misses, weights, calibration */
    float    minDepartureMps;     /* range rate the second point must show */
    float    maxSpeedMps;         /* physical ceiling; faster is not a ball */
    float    originGateBins;      /* acquire within this many bins beyond the origin */
    float    minDepartureBins;    /* ... and at least this many beyond it: the impact
                                   * echo and the resting club sit at the origin */
    uint32_t launchPoints;        /* earliest points fitted for the launch */
    float    snr;                 /* extraction threshold over the floor for the post
                                   * window: a departing ball is a weak return */
} l3_ball_track_cfg_t;

enum {
    L3_BALL_TRACK_WHY_NONE = 0,
    L3_BALL_TRACK_WHY_UNARMED,     /* no impact yet */
    L3_BALL_TRACK_WHY_NO_CANDIDATE,/* nothing beyond the origin */
    L3_BALL_TRACK_WHY_ACQUIRED,
    L3_BALL_TRACK_WHY_CONFIRMED,   /* the second point departs fast enough */
    L3_BALL_TRACK_WHY_TOO_SLOW,    /* it did not: dropped, searching again */
    L3_BALL_TRACK_WHY_TOO_FAST,    /* faster than any ball: dropped */
    L3_BALL_TRACK_WHY_TRACKED,
    L3_BALL_TRACK_WHY_COASTED,
    L3_BALL_TRACK_WHY_LOST,        /* the flight left the window or the track dropped */
    L3_BALL_TRACK_WHY_COUNT
};

typedef struct {
    l3_ball_track_cfg_t cfg;
    l3_club_track_t core;
    uint8_t   armed;
    uint8_t   confirmed;
    uint8_t   why;
    uint8_t   done;               /* nothing more will be added: lost after confirmation */
    uint32_t  impactTimestampUs;
    float     originBin;          /* global bin of the ball at impact */
    l3_vec3_t origin;             /* golf frame */
    uint32_t  lastTargetIndex;    /* index into the last update's targets that was
                                   * appended, L3_TRACK_NO_TARGET when none */
    uint32_t  counters[L3_BALL_TRACK_WHY_COUNT];
} l3_ball_track_t;

/* Ball launch from the earliest clean flight, extrapolated to impact. */
typedef struct {
    uint32_t  points;
    l3_vec3_t velocity;           /* m/s, golf frame */
    l3_vec3_t launchPosition;     /* the fitted line at the impact time */
    float     speedMps;
    float     radialSpeedMps;     /* range-only fit, for cross-checking */
    float     hlaRad;             /* horizontal launch, positive right */
    float     vlaRad;             /* vertical launch, positive up */
    float     residualM;
    float     confidence;
    uint8_t   speedValid;
    uint8_t   hlaValid;
    uint8_t   vlaValid;
} l3_launch_t;

void l3_ball_track_cfg_defaults(l3_ball_track_cfg_t *cfg);
void l3_ball_track_init(l3_ball_track_t *track, const l3_ball_track_cfg_t *cfg);
/* Forget the flight and the arming; configuration and counters survive. */
void l3_ball_track_reset(l3_ball_track_t *track);
/* IMPACT: start looking for a ball leaving originBin (global) at origin. */
void l3_ball_track_arm(l3_ball_track_t *track, float originBin, const l3_vec3_t *origin,
                       uint32_t impactTimestampUs);
/* One post-impact frame's targets. Returns 1 when a point was appended. */
int32_t l3_ball_track_update(l3_ball_track_t *track, const l3_target_obs_t *targets, uint32_t n,
                             uint32_t frame, uint32_t timestampUs);
/* Angles for the point the last update appended; see l3_track_set_angles. */
int32_t l3_ball_track_set_angles(l3_ball_track_t *track, float azimuthRad, float elevationRad,
                                 uint8_t anglesValid);
/* The launch from the earliest cfg.launchPoints confirmed points (at least
 * 3). Returns the points used, 0 when too few. */
uint32_t l3_ball_track_launch(const l3_ball_track_t *track, l3_launch_t *out);
const char *l3_ball_track_why_name(uint8_t why);
/* "balltrack armed=1 confirmed=1 done=0 why=tracked count=5 origin=47.0 ..." */
int32_t l3_ball_track_format_status(const l3_ball_track_t *track, char *out, uint32_t cap);
/* "launch points=5 speed=61.20 radial=58.90 hla=1.20 vla=12.40 residualmm=... conf=... valid=shv" */
int32_t l3_launch_format(const l3_launch_t *launch, char *out, uint32_t cap);

#endif /* L3_BALL_TRACK_H */
