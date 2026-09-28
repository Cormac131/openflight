/* IWR6843 joint club/ball path search after impact.
 *
 * After the impact gate fires, two independent beams (club and ball) are
 * maintained over a sliding window of L3_JOINT_WINDOW frames. Club is scored
 * first; surviving club hypotheses claim their targets, and the ball beam
 * cannot reuse those indices. A later frame can still revise earlier
 * assignments within each beam's own window.
 *
 * Nodes stay compact: path state (lastBin, lastTimeUs) is recovered by
 * chain-walking the window rather than duplicating it in every node.
 *
 * Pure C, no hardware.
 */
#ifndef L3_JOINT_SEARCH_H
#define L3_JOINT_SEARCH_H

#include <stdint.h>

#include "l3_club_track.h"   /* l3_radar_cal_t, l3_track_point_t, l3_delivery_t */
#include "l3_launch.h"       /* l3_launch_t */
#include "l3_observation.h"  /* l3_target_obs_t */

/* --------------------------------------------------------------------------
 * Constants
 * -------------------------------------------------------------------------- */
#define L3_JOINT_CLUB_BEAM     4U   /* club hypotheses kept per frame */
#define L3_JOINT_BALL_BEAM     4U   /* ball hypotheses kept per frame */
#define L3_JOINT_BEAM          8U   /* club + ball, cap for now().beamSize */
#define L3_JOINT_WINDOW        8U   /* frames kept in sliding window */
#define L3_JOINT_BALL_POINTS   8U   /* ball path capacity (for launch fit) */
#define L3_JOINT_NONE       0xFFU   /* sentinel: no target assigned */

/* Path life-cycle states. */
enum {
    L3_JOINT_UNSTARTED = 0,
    L3_JOINT_ACTIVE    = 1,
    L3_JOINT_ENDED     = 2
};

/* Role of a target in the best explanation's latest frame. */
enum {
    L3_JOINT_USE_NONE  = 0,
    L3_JOINT_USE_CLUB  = 1,
    L3_JOINT_USE_BALL  = 2
};

/* Diagnostic counter indices. */
enum {
    L3_JOINT_CNT_PAIRINGS   = 0,
    L3_JOINT_CNT_CONFIRMED  = 1,
    L3_JOINT_CNT_FORCED_OUT = 2,
    L3_JOINT_CNT_SKIPPED    = 3,
    L3_JOINT_CNT_NONFINITE  = 4,
    L3_JOINT_CNT_ANGLE_EST  = 5,
    L3_JOINT_N_COUNTERS     = 6
};

/* --------------------------------------------------------------------------
 * Config
 * -------------------------------------------------------------------------- */
typedef struct {
    /* Gate geometry */
    float    binWidthM;
    float    velocitySpanMps;
    float    startBehindBins;
    float    startBeyondBins;

    /* Ball physics */
    float    ballMinSpeedMps;
    float    ballMaxSpeedMps;
    float    ballAccelMps2;
    uint32_t ballMaxMisses;
    uint32_t ballMinPoints;

    /* Club physics */
    float    clubMinSpeedMps;
    float    clubMaxSpeedMps;
    float    clubDecelMps2;
    float    clubAccelMps2;
    uint32_t clubMaxMisses;

    /* Scoring */
    float    rangeSigmaBins;
    float    coastSigmaGrowBins;
    float    termCap;
    float    missCost;
    float    clubStrongerBonus;

    /* Confirmation */
    float    confirmMargin;
    float    maxOriginCrossMs;
    float    maxResidualBins;

    /* Calibration */
    l3_radar_cal_t cal;

    /* Tangent/neutral — deferred, zero = disabled */
    float    tangentBonus;
    float    neutralBonus;
    float    tangentToleranceRad;
} l3_joint_cfg_t;

/* --------------------------------------------------------------------------
 * Kinematic seed from the pre-impact club track
 * -------------------------------------------------------------------------- */
typedef struct {
    float    rangeBin;
    float    speedMps;
    uint32_t timestampUs;
} l3_joint_kin_t;

/* --------------------------------------------------------------------------
 * Compact per-path nodes. Path state is recovered by walking the parent
 * chain; only the resolved speed is stored for the next Doppler unwrap.
 * -------------------------------------------------------------------------- */
typedef struct {
    uint8_t  target;          /* index into frame targets, or L3_JOINT_NONE */
    uint8_t  parent;          /* index in previous slot, or L3_JOINT_NONE */
    uint8_t  misses;
    uint8_t  state;           /* L3_JOINT_UNSTARTED/ACTIVE/ENDED */
    float    score;           /* running total (lower = better) */
    float    speedMps;
} l3_joint_club_node_t;       /* 12 bytes */

typedef struct {
    uint8_t  target;
    uint8_t  parent;
    uint8_t  misses;
    uint8_t  state;
    uint8_t  speedKnown;      /* 0 until second ball point resolves alias */
    uint8_t  hits;            /* real associations on this path */
    uint8_t  _pad[2];
    float    score;
    float    speedMps;
} l3_joint_ball_node_t;       /* 16 bytes */

/* --------------------------------------------------------------------------
 * One slot in the window: nodes + compact target copies
 * -------------------------------------------------------------------------- */
typedef struct {
    l3_joint_club_node_t nodes[L3_JOINT_CLUB_BEAM];
    uint32_t             count;
    uint32_t             frame;
    uint32_t             timestampUs;
} l3_joint_club_link_t;

typedef struct {
    l3_joint_ball_node_t nodes[L3_JOINT_BALL_BEAM];
    uint32_t             count;
    uint32_t             frame;
    uint32_t             timestampUs;
} l3_joint_ball_link_t;

typedef struct {
    float    rangeBin;
    float    dopplerAliasMps;
    float    snr;
    float    azimuthRad;
    float    elevationRad;
    uint8_t  anglesValid;
    uint8_t  _pad[3];
} l3_joint_frame_target_t;

typedef struct {
    l3_joint_frame_target_t targets[L3_OBS_MAX_TARGETS];
    uint32_t                count;
    uint32_t                timestampUs;
} l3_joint_frame_t;

/* --------------------------------------------------------------------------
 * Finished points (written out as the window drains)
 * -------------------------------------------------------------------------- */
typedef struct {
    float    rangeBin;
    float    speedMps;
    float    azimuthRad;
    float    elevationRad;
    uint32_t timestampUs;
    uint8_t  anglesValid;
    uint8_t  _pad[3];
} l3_joint_point_t;

typedef struct {
    float    rangeBin;
    float    speedMps;
    float    azimuthRad;
    float    elevationRad;
    uint32_t timestampUs;
    uint8_t  anglesValid;
    uint8_t  _pad[3];
} l3_joint_ball_point_t;

/* --------------------------------------------------------------------------
 * Angle estimate request
 * -------------------------------------------------------------------------- */
typedef struct {
    uint8_t  frameSlot;
    uint8_t  targetIdx;
    uint8_t  needed;
    uint8_t  _pad;
} l3_joint_angle_req_t;

/* --------------------------------------------------------------------------
 * "Now" snapshot (best explanation view for host / replay)
 * -------------------------------------------------------------------------- */
typedef struct {
    float    clubBin;
    float    ballBin;
    float    clubPredBin;
    float    ballPredBin;
    float    bestScore;
    uint32_t beamSize;
    uint8_t  ballConfirmed;
    uint8_t  _pad[3];
} l3_joint_now_t;

/* --------------------------------------------------------------------------
 * Main state struct
 * -------------------------------------------------------------------------- */
typedef struct {
    l3_joint_cfg_t cfg;

    l3_joint_club_link_t clubLinks[L3_JOINT_WINDOW];
    l3_joint_ball_link_t ballLinks[L3_JOINT_WINDOW];
    l3_joint_frame_t     frames[L3_JOINT_WINDOW];
    uint32_t             winHead;
    uint32_t             winSize;

    l3_joint_point_t      clubPoints[L3_JOINT_BALL_POINTS];
    uint32_t              clubCount;
    l3_joint_ball_point_t ballPoints[L3_JOINT_BALL_POINTS];
    uint32_t              ballCount;

    uint8_t  ballConfirmed;
    uint8_t  _pad[3];
    uint32_t confirmFirstBallTarget[4];
    uint32_t gateTimestampUs;

    l3_joint_kin_t seed;
    uint8_t        seedValid;
    uint8_t        _pad2[3];

    uint32_t counters[L3_JOINT_N_COUNTERS];
} l3_joint_t;

/* --------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */
void     l3_joint_cfg_defaults(l3_joint_cfg_t *cfg);
void     l3_joint_init(l3_joint_t *js, const l3_joint_cfg_t *cfg);
void     l3_joint_reset(l3_joint_t *js);
void     l3_joint_arm(l3_joint_t *js, const l3_joint_kin_t *seed,
                      uint32_t gateTimestampUs);

int32_t  l3_joint_update(l3_joint_t *js, uint32_t frame, uint32_t timestampUs,
                         const l3_target_obs_t *targets, uint32_t n);
void     l3_joint_finish(l3_joint_t *js);

uint32_t l3_joint_angle_requests(l3_joint_t *js,
                                 l3_joint_angle_req_t *out, uint32_t maxOut);
void     l3_joint_set_angles(l3_joint_t *js,
                             const l3_joint_angle_req_t *reqs, uint32_t n);

l3_joint_now_t l3_joint_now(const l3_joint_t *js);
uint32_t l3_joint_target_use(const l3_joint_t *js, uint32_t targetIdx);
uint32_t l3_joint_window_ball(const l3_joint_t *js,
                              l3_joint_ball_point_t *out, uint32_t maxOut);
int32_t  l3_joint_ball_point(const l3_joint_t *js, uint32_t slotIdx,
                             l3_joint_ball_point_t *out);
int32_t  l3_joint_launch(const l3_joint_t *js, const l3_delivery_t *clubFit,
                         uint32_t impactTimestampUs, l3_launch_t *out);
uint32_t l3_joint_struct_bytes(void);

#endif /* L3_JOINT_SEARCH_H */
