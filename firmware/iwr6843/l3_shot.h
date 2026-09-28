/* IWR6843 shot state machine.
 *
 * One explicit sequence per shot, so the candidate-selection rules can change
 * with the phase instead of one generic target definition serving the whole
 * shot: before impact the approaching club is what matters, after it the
 * rapidly departing ball.
 *
 *   WAITING_FOR_BALL -> READY -> CLUB_ACQUIRE -> CLUB_TRACK -> IMPACT
 *       -> BALL_TRACK -> SOLVE -> RESULT -> (rearm) WAITING_FOR_BALL
 *
 * READY needs a locked ball unless the configuration lets the tee stand in
 * (the motion-only fallback). CLUB_ACQUIRE is the first club point,
 * CLUB_TRACK a track with a range rate. A dropped track returns to READY. The
 * range gate or the geometric detector firing enters IMPACT, which freezes
 * the ball origin, the club delivery, the impact time and the club
 * trajectory: everything after reads those, never the live trackers. The
 * first post-impact frame enters BALL_TRACK; the ball tracker finishing or
 * the post movie ending enters SOLVE; the solver reporting enters RESULT.
 * Pure C, no hardware.
 */
#ifndef L3_SHOT_H
#define L3_SHOT_H

#include <stdint.h>

#include "l3_club_track.h"
#include "l3_frames.h"

enum {
    L3_SHOT_WAITING_FOR_BALL = 0,
    L3_SHOT_READY,
    L3_SHOT_CLUB_ACQUIRE,
    L3_SHOT_CLUB_TRACK,
    L3_SHOT_IMPACT,
    L3_SHOT_BALL_TRACK,
    L3_SHOT_SOLVE,
    L3_SHOT_RESULT,
    L3_SHOT_STATE_COUNT
};

/* impactSource bits */
#define L3_SHOT_IMPACT_GATE      1U
#define L3_SHOT_IMPACT_GEOMETRY  2U
#define L3_SHOT_IMPACT_RANGE     4U

typedef struct {
    uint8_t  requireBall;      /* 1: READY needs a locked ball; 0: the tee stands in */
    uint32_t ballTrackFrames;  /* post-impact frames before SOLVE regardless of the tracker */
} l3_shot_cfg_t;

/* What the machine reads each frame. Pointers may be NULL when unknown. */
typedef struct {
    uint8_t   ballLocked;
    l3_vec3_t ballPosition;        /* golf frame; the tee when not locked */
    uint8_t   clubActive;          /* club track has a track */
    uint32_t  clubPoints;          /* points it holds */
    uint8_t   gateFired;           /* range gate fired this frame */
    uint8_t   geometricFired;      /* geometric detector fired this frame */
    uint32_t  impactTimestampUs;   /* the geometric detector's time, else the frame's */
    const l3_delivery_t   *delivery;
    const l3_club_track_t *club;
    uint8_t   postFrame;           /* this frame is a post-impact frame */
    uint8_t   ballTrackDone;       /* the ball tracker has all it will get */
    uint8_t   solved;              /* the solver has produced a result */
    uint8_t   rangeFired;          /* range-only impact fired this frame */
} l3_shot_input_t;

typedef struct {
    l3_shot_cfg_t cfg;
    uint8_t   state;
    uint8_t   previous;
    uint32_t  enteredFrame;
    uint32_t  transitions;
    uint32_t  postFrames;          /* post-impact frames seen since IMPACT */
    /* Frozen at IMPACT. */
    uint8_t   impactSource;        /* L3_SHOT_IMPACT_* bits */
    uint32_t  impactFrame;
    uint32_t  impactTimestampUs;
    l3_vec3_t ballOrigin;
    l3_delivery_t delivery;
    uint32_t  clubPoints;
    l3_track_point_t clubTrajectory[L3_TRACK_POINTS];
    uint32_t  entries[L3_SHOT_STATE_COUNT];
} l3_shot_t;

void l3_shot_cfg_defaults(l3_shot_cfg_t *cfg);
void l3_shot_init(l3_shot_t *shot, const l3_shot_cfg_t *cfg);
/* Back to WAITING_FOR_BALL for the next shot; counters survive, the frozen
 * impact record is cleared. */
void l3_shot_rearm(l3_shot_t *shot);
/* One frame. Returns the state after it. */
uint8_t l3_shot_update(l3_shot_t *shot, const l3_shot_input_t *in, uint32_t frame);
/* 1 once impact has been declared: the ball, not the club, is the target. */
int32_t l3_shot_wants_departing(const l3_shot_t *shot);
const char *l3_shot_state_name(uint8_t state);
/* "none", "gate", "geometry", "both" (gate+geometry), else the '+'-joined
 * names of the bits set, in the order gate, geometry, range. */
const char *l3_shot_source_name(uint8_t source, char *buf, uint32_t cap);
/* "shot state=club_track since=17 impact=- source=none origin=1.36,0.00,0.00 club=7" */
int32_t l3_shot_format(const l3_shot_t *shot, char *out, uint32_t cap);

#endif /* L3_SHOT_H */
