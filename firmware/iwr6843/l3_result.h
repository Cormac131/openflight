/* IWR6843 shot result: measurements with confidence, validation and the
 * versioned packet the firmware hands the Pi.
 *
 * Every metric is an l3_measurement_t: a value, a 0..1 confidence and flags
 * saying whether it is valid, measured rather than inferred, radial-only or
 * implausible. The Pi decides what to display from those; bad data is never
 * turned into a precise-looking number. Before a shot is published its
 * evidence is checked (ball locked, club track valid, impact identified, ball
 * flight from the ball's origin, continuous trajectories, plausible speeds,
 * fit residuals, plausible angles, plausible smash) and the shot is VALID,
 * PARTIAL (ball speed and launch without the club, or the club without the
 * flight) or INVALID.
 *
 * The packet layout is fixed and little-endian (l3_result_serialize writes
 * it byte by byte, so the host's shot_result.py can parse it without knowing
 * the compiler's struct padding). Version 1: see L3_RESULT_PACKET_BYTES.
 * Pure C, no hardware.
 */
#ifndef L3_RESULT_H
#define L3_RESULT_H

#include <stdint.h>

#include "l3_ball_track.h"
#include "l3_club_track.h"
#include "l3_shot.h"

#define L3_RESULT_VERSION      1U
#define L3_RESULT_METRICS      9U
#define L3_RESULT_PACKET_BYTES 100U

/* Metric indices, also the bit positions of validFlags. */
enum {
    L3_METRIC_BALL_SPEED = 0,
    L3_METRIC_VERTICAL_LAUNCH,
    L3_METRIC_HORIZONTAL_LAUNCH,
    L3_METRIC_CLUB_SPEED,
    L3_METRIC_CLUB_PATH,
    L3_METRIC_ANGLE_OF_ATTACK,
    L3_METRIC_SPIN_RATE,
    L3_METRIC_SPIN_AXIS,
    L3_METRIC_IMPACT_RANGE
};

/* l3_measurement_t flags */
#define L3_MEAS_VALID        1U   /* a value exists */
#define L3_MEAS_MEASURED     2U   /* measured by the radar, not inferred or modelled */
#define L3_MEAS_RADIAL_ONLY  4U   /* speed from the range walk alone (no angles) */
#define L3_MEAS_IMPLAUSIBLE  8U   /* outside the physical bounds below */
#define L3_MEAS_FALLBACK     16U  /* the tee stood in for a locked ball */

/* qualityFlags */
#define L3_QUALITY_BALL_LOCKED          1U
#define L3_QUALITY_CLUB_TRACK           2U
#define L3_QUALITY_IMPACT_IDENTIFIED    4U
#define L3_QUALITY_BALL_FROM_ORIGIN     8U
#define L3_QUALITY_CLUB_CONTINUOUS      16U
#define L3_QUALITY_BALL_CONTINUOUS      32U
#define L3_QUALITY_SPEEDS_PLAUSIBLE     64U
#define L3_QUALITY_RESIDUALS_OK         128U
#define L3_QUALITY_ANGLES_PLAUSIBLE     256U
#define L3_QUALITY_SMASH_PLAUSIBLE      512U
#define L3_QUALITY_GEOMETRIC_IMPACT     1024U

enum {
    L3_RESULT_INVALID = 0,
    L3_RESULT_PARTIAL = 1,
    L3_RESULT_VALID = 2
};

/* Physical bounds; a value outside is flagged implausible, never clipped. */
#define L3_RESULT_CLUB_SPEED_MIN_MPS   5.0F
#define L3_RESULT_CLUB_SPEED_MAX_MPS   70.0F
#define L3_RESULT_BALL_SPEED_MIN_MPS   5.0F
#define L3_RESULT_BALL_SPEED_MAX_MPS   100.0F
#define L3_RESULT_SMASH_MIN            0.8F
#define L3_RESULT_SMASH_MAX            1.6F
#define L3_RESULT_VLA_MIN_RAD          (-0.1745F)   /* -10 deg */
#define L3_RESULT_VLA_MAX_RAD          1.0472F      /*  60 deg */
#define L3_RESULT_HLA_MAX_RAD          0.7854F      /* +/-45 deg */
#define L3_RESULT_PATH_MAX_RAD         0.5236F      /* +/-30 deg */
#define L3_RESULT_AOA_MAX_RAD          0.3491F      /* +/-20 deg */
#define L3_RESULT_RESIDUAL_MAX_M       0.06F        /* about a bin and a quarter */
#define L3_RESULT_MIN_CLUB_POINTS      3U
#define L3_RESULT_MIN_BALL_POINTS      3U

typedef struct {
    float    value;
    float    confidence;
    uint32_t flags;
} l3_measurement_t;

typedef struct {
    uint32_t version;
    uint32_t shotId;
    l3_measurement_t metric[L3_RESULT_METRICS];  /* m/s and radians */
    uint32_t validFlags;       /* bit i: metric i valid */
    uint32_t qualityFlags;     /* L3_QUALITY_* */
    uint32_t impactTimestampUs;
    uint8_t  verdict;          /* L3_RESULT_* */
    uint8_t  impactSource;     /* L3_SHOT_IMPACT_* bits */
    uint8_t  clubPoints;
    uint8_t  ballPoints;
    float    smash;            /* ball speed / club speed, 0 when either is missing */
} l3_shot_result_t;

/* Assemble the result from the frozen shot, the ball track's launch and the
 * shot's frozen delivery. spin is left invalid until it is measured. */
void l3_result_build(const l3_shot_t *shot, const l3_ball_track_t *ball, const l3_launch_t *launch,
                     uint32_t shotId, uint8_t ballLocked, l3_shot_result_t *out);
/* Little-endian packet, L3_RESULT_PACKET_BYTES long. Returns the bytes written,
 * 0 when cap is too small. */
uint32_t l3_result_serialize(const l3_shot_result_t *result, uint8_t *out, uint32_t cap);
const char *l3_result_metric_name(uint32_t index);
const char *l3_result_verdict_name(uint8_t verdict);
/* "result shot=3 verdict=valid quality=0x7ff impact=23218 source=geometry smash=1.46" */
int32_t l3_result_format(const l3_shot_result_t *result, char *out, uint32_t cap);
/* "  ball_speed=61.20 conf=0.91 flags=measured" (angles in degrees) */
int32_t l3_result_format_metric(const l3_shot_result_t *result, uint32_t index, char *out,
                                uint32_t cap);
/* The packet as hex text, two characters per byte. */
int32_t l3_result_format_hex(const l3_shot_result_t *result, char *out, uint32_t cap);

#endif /* L3_RESULT_H */
