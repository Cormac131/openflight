/* IWR6843 range-only impact: what fires the self-trigger.
 *
 * The club track's club-in line (l3_impact_fit_track, fitted to its range
 * points) crosses the ball's range at a time between frames. Impact is
 * declared when that crossing lies within a horizon of the current frame's
 * time, which also dates impact between frames rather than to a frame index.
 * The club coasts across the tee band, so the newest point stops advancing
 * and the frame clock must.
 *
 * A geometric detector once sat beside it, judging the club's 3D line against
 * the ball's position; it was removed on 2026-09-30 with the range gate: the
 * kiosk never armed it and it never fired on the recorded swings. Pure C, no
 * hardware.
 */
#ifndef L3_IMPACT_H
#define L3_IMPACT_H

#include <stdint.h>

#include "l3_impact_fit.h"

typedef struct {
    float horizonS;       /* fire when the crossing is within this of the frame's time */
} l3_impact_cfg_t;

enum {
    L3_IMPACT_WHY_NONE = 0,
    L3_IMPACT_WHY_NO_DELIVERY,   /* no usable club-in estimate */
    L3_IMPACT_WHY_PENDING,       /* crossing still beyond the horizon */
    L3_IMPACT_WHY_PASSED,        /* crossing more than a horizon ago: missed */
    L3_IMPACT_WHY_FIRED,
    L3_IMPACT_WHY_COUNT
};

typedef struct {
    l3_impact_cfg_t cfg;
    uint8_t   fired;
    uint8_t   why;                /* last update */
    float     offsetS;            /* crossing time relative to the frame (+ ahead) */
    uint32_t  impactTimestampUs;  /* the crossing, when fired */
    uint32_t  counters[L3_IMPACT_WHY_COUNT];
} l3_impact_t;

void l3_impact_cfg_defaults(l3_impact_cfg_t *cfg);
void l3_impact_init(l3_impact_t *impact, const l3_impact_cfg_t *cfg);
/* Forget a fire so the detector can fire again; counters survive. */
void l3_impact_rearm(l3_impact_t *impact);
const char *l3_impact_why_name(uint8_t why);
/* Fire on the club-in estimate when its crossing of the ball's range is
 * within the horizon of nowUs, the current frame's time. A missing or
 * rejected estimate is nodelivery. Returns 1 on the update that fires;
 * later updates are ignored until l3_impact_rearm. */
int32_t l3_impact_update_range(l3_impact_t *impact, const l3_fit_estimate_t *clubIn,
                               uint32_t nowUs);
/* "impact fired=1 why=fired offsetms=2.50 t=30000 pending=1 passed=0 fired_n=1" */
int32_t l3_impact_format(const l3_impact_t *impact, char *out, uint32_t cap);

#endif /* L3_IMPACT_H */
