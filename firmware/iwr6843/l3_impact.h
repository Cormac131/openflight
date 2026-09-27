/* IWR6843 geometric impact detector.
 *
 * The range gate (l3_trigger.c) fires when the club's range bin enters a
 * window around the ball's bin. This detector fires on geometry instead: the
 * club delivery (l3_club_track.c) is a position and a velocity in the GOLF
 * frame, the ball is a position in the same frame, so the closest approach
 * of the club's line to the ball and the moment it happens follow directly.
 * Impact is declared when that closest approach comes within a tolerance of
 * the ball and its time lies within a horizon of the newest observation,
 * which also gives the impact time between frames rather than a frame index.
 * A practice swing that misses the ball by more than the tolerance never
 * fires; a body walking through the lane is too slow to. The range gate is
 * kept as the fallback until this has proved itself on hardware; l3_dump.c
 * fires on either and records which. Pure C, no hardware.
 */
#ifndef L3_IMPACT_H
#define L3_IMPACT_H

#include <stdint.h>

#include "l3_club_track.h"
#include "l3_frames.h"

typedef struct {
    float toleranceM;     /* closest approach that counts as contact */
    float horizonS;       /* fire when contact is predicted within this of the newest point */
    float minSpeedMps;    /* a delivery slower than this is not a swing */
    float minConfidence;  /* delivery confidence required */
} l3_impact_cfg_t;

enum {
    L3_IMPACT_WHY_NONE = 0,
    L3_IMPACT_WHY_NO_BALL,       /* no ball position to aim at */
    L3_IMPACT_WHY_NO_DELIVERY,   /* the club track has no usable fit */
    L3_IMPACT_WHY_SLOW,          /* fitted speed under minSpeedMps */
    L3_IMPACT_WHY_UNSURE,        /* delivery confidence under minConfidence */
    L3_IMPACT_WHY_FAR,           /* the line misses the ball by more than the tolerance */
    L3_IMPACT_WHY_PENDING,       /* on course, contact still beyond the horizon */
    L3_IMPACT_WHY_PASSED,        /* contact was more than a horizon ago: missed */
    L3_IMPACT_WHY_FIRED,
    L3_IMPACT_WHY_COUNT
};

typedef struct {
    l3_impact_cfg_t cfg;
    uint8_t   fired;
    uint8_t   why;                /* last update */
    float     closestM;           /* closest approach of the fitted line to the ball */
    float     offsetS;            /* contact time relative to the newest point (+ ahead) */
    uint32_t  impactTimestampUs;  /* newest point time + offset, when fired */
    l3_vec3_t contact;            /* club position at closest approach */
    l3_vec3_t velocity;           /* delivery velocity at the fire */
    uint32_t  counters[L3_IMPACT_WHY_COUNT];
} l3_impact_t;

void l3_impact_cfg_defaults(l3_impact_cfg_t *cfg);
void l3_impact_init(l3_impact_t *impact, const l3_impact_cfg_t *cfg);
/* Forget a fire so the detector can fire again; counters survive. */
void l3_impact_rearm(l3_impact_t *impact);
/* Closest approach of the line position + velocity * t to ball: the distance,
 * the time offset t (seconds, relative to the position's time) and the point
 * on the line. A zero velocity gives the distance to position at t = 0. */
void l3_impact_closest(const l3_vec3_t *position, const l3_vec3_t *velocity,
                       const l3_vec3_t *ball, float *distanceM, float *offsetS,
                       l3_vec3_t *contact);
/* Judge one delivery. ballValid 0 means no locked ball. Returns 1 on the
 * update that fires; later updates are ignored until l3_impact_rearm. */
int32_t l3_impact_update(l3_impact_t *impact, const l3_delivery_t *delivery,
                         const l3_vec3_t *ball, uint8_t ballValid);
const char *l3_impact_why_name(uint8_t why);
/* "impact fired=1 why=fired closest=0.031 offset=-0.0011 t=123456 ..." */
int32_t l3_impact_format(const l3_impact_t *impact, char *out, uint32_t cap);

#endif /* L3_IMPACT_H */
