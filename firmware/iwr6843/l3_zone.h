/* IWR6843 swing zone: is a measured point inside the corridor around the tee?
 *
 * The self-trigger works in range only: anything moving in the bins short of
 * the ball counts, whatever its direction, and at 2 m the radar sees a ring
 * some 3.4 m wide (the golfer's body, a bystander, the bay walls). The zone
 * is a box in the GOLF frame (l3_frames.h: x along the target line, y right
 * of it, z up, metres from the antenna) around the tee:
 *
 *     teeForwardM - shortM <= x <= teeForwardM + pastM
 *     |y - lateralM|       <= halfWidthM
 *     minHeightM <= z + radarHeightM <= maxHeightM     (heights above the floor)
 *
 * l3_zone_check names every limit a point breaks, as reason bits, so a
 * replay or a log can say why a track was refused. Nothing calls it on the
 * board yet: the limits are set from the bench azimuth check and the
 * recorded swings first (docs/iwr6843/azimuth-check.md). Pure C, no hardware.
 */
#ifndef L3_ZONE_H
#define L3_ZONE_H

#include <stdint.h>

#include "l3_frames.h"

typedef struct {
    float teeForwardM;     /* the tee's x in the golf frame */
    float lateralM;        /* the corridor's centre line, y; 0 is the target line */
    float shortM;          /* the corridor starts this far short of the tee ... */
    float pastM;           /* ... and ends this far past it */
    float halfWidthM;      /* either side of the centre line */
    float radarHeightM;    /* the antenna above the floor */
    float minHeightM;      /* above the floor */
    float maxHeightM;
    uint8_t requireAngles; /* 1: a point without both angles is outside */
} l3_zone_cfg_t;

/* Reason bits: 0 is inside. */
#define L3_ZONE_INSIDE    0U
#define L3_ZONE_NO_ANGLES 1U   /* azimuth or elevation not measured */
#define L3_ZONE_SHORT     2U   /* nearer the radar than the corridor */
#define L3_ZONE_PAST      4U   /* beyond it */
#define L3_ZONE_LEFT      8U
#define L3_ZONE_RIGHT     16U
#define L3_ZONE_LOW       32U
#define L3_ZONE_HIGH      64U
#define L3_ZONE_REASONS   7U   /* how many bits there are */

/* The angle bits a point carries (l3_observation.h L3_OBS_ANGLE_*). */
#define L3_ZONE_ANGLES_BOTH 3U

/* Starting values for a tee at teeForwardM, not calibrated limits: the
 * corridor runs 1.2 m short of the tee to 0.3 m past it, 0.3 m either side of
 * the target line, from 0.1 m under the floor (a measurement's spread) to
 * 1.2 m above it, and needs both angles. */
void l3_zone_cfg_defaults(l3_zone_cfg_t *cfg, float teeForwardM, float radarHeightM);
/* 0 when the cfg can describe a corridor: finite values, shortM, pastM and
 * halfWidthM >= 0, minHeightM <= maxHeightM. */
int32_t l3_zone_cfg_check(const l3_zone_cfg_t *cfg);
/* The reason bits for a golf-frame point carrying anglesValid
 * (L3_OBS_ANGLE_* bits); L3_ZONE_INSIDE when it is inside. Without both
 * angles the position is boresight-filled and only the range limits are
 * judged, plus L3_ZONE_NO_ANGLES when requireAngles is set. */
uint32_t l3_zone_check(const l3_zone_cfg_t *cfg, const l3_vec3_t *golf, uint8_t anglesValid);
/* "short", "left", ... for one bit; "?" for anything else. */
const char *l3_zone_reason_name(uint32_t bit);

#endif /* L3_ZONE_H */
