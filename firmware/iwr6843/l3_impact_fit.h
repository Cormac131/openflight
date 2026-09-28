/* IWR6843 impact from the tracks either side of the tee band.
 *
 * Inside the band (l3_band.h) the MTI ridge hides impact; outside it three
 * clean tracks remain: the club approaching (club in), the club carrying on
 * (club out) and the ball leaving (ball out). Each is fitted as a straight
 * line in range against time over the K points nearest the band and solved
 * for the moment it passes the ball's range, with an uncertainty that grows
 * with the extrapolation. Physics checks drop a track that cannot be what it
 * claims; the survivors are fused by inverse variance and their agreement is
 * the confidence. See docs/superpowers/specs/
 * 2026-09-28-iwr-impact-back-interpolation-design.md. Pure C, no hardware.
 */
#ifndef L3_IMPACT_FIT_H
#define L3_IMPACT_FIT_H

#include <stdint.h>

#include "l3_club_track.h"

#define L3_FIT_MAX_POINTS 8U
#define L3_FIT_NO_TRACK   0xFFU

enum { L3_FIT_CLUB_IN = 0, L3_FIT_CLUB_OUT, L3_FIT_BALL_OUT, L3_FIT_TRACKS };

enum {
    L3_FIT_WHY_OK = 0,
    L3_FIT_WHY_MISSING,          /* no points */
    L3_FIT_WHY_FEW_POINTS,       /* under minPoints */
    L3_FIT_WHY_WRONG_DIRECTION,  /* not moving downrange */
    L3_FIT_WHY_SPEED_BOUNDS,     /* outside this track's speed bounds */
    L3_FIT_WHY_PHYSICS,          /* contradicts another track (smash, club slowing) */
    L3_FIT_WHY_NONFINITE,        /* times do not spread, or the fit overflowed */
    L3_FIT_WHY_DROPPED,          /* the outlier of three */
    L3_FIT_WHY_COUNT
};

enum {
    L3_FIT_VERDICT_NONE = 0,
    L3_FIT_VERDICT_SINGLE,
    L3_FIT_VERDICT_CONSISTENT,
    L3_FIT_VERDICT_INCONSISTENT,
    L3_FIT_VERDICT_COUNT
};

typedef struct {
    float    binWidthM;
    float    bandBins;          /* the tee band's half width */
    uint32_t fitPoints;         /* K nearest the band */
    uint32_t minPoints;
    float    clubMinMps;        /* club in */
    float    clubMaxMps;        /* club in and club out */
    float    clubOutMaxRatio;   /* club out no faster than club in times this */
    float    ballMinMps;
    float    ballMaxMps;
    float    gateSigmas;        /* agreement gate ... */
    float    minSigmaUs;        /* ... on at least this sigma */
} l3_impact_fit_cfg_t;

typedef struct {
    uint8_t  why;               /* L3_FIT_WHY_* */
    uint32_t points;            /* points fitted (or offered, when too few) */
    float    timeUs;            /* when the line passes the ball's range */
    float    sigmaUs;
    float    speedMps;          /* range rate, positive downrange */
} l3_fit_estimate_t;

typedef struct {
    l3_fit_estimate_t track[L3_FIT_TRACKS];
    uint8_t  verdict;           /* L3_FIT_VERDICT_* */
    uint8_t  droppedTrack;      /* L3_FIT_NO_TRACK or the dropped index */
    uint8_t  noLock;            /* the configured tee stood in for the ball */
    float    impactUs;          /* 0 with verdict none */
    float    spreadUs;          /* max - min of the estimates kept */
    float    refinedMinusTriggerUs;
} l3_impact_fit_t;

/* A point list read by index: an array ... */
typedef struct {
    const l3_track_point_t *points;
    uint32_t count;
} l3_fit_list_t;
/* ... or a run of a track's held points, oldest first. */
typedef struct {
    const l3_club_track_t *track;
    uint32_t first;
    uint32_t count;
} l3_fit_span_t;

void l3_impact_fit_cfg_defaults(l3_impact_fit_cfg_t *cfg);
/* Every track missing, verdict none, nothing dropped. */
void l3_impact_fit_reset(l3_impact_fit_t *fit);
/* l3_point_at_fn readers for the two kinds of list. */
int32_t l3_fit_list_point(const void *ctx, uint32_t index, l3_track_point_t *out);
int32_t l3_fit_span_point(const void *ctx, uint32_t index, l3_track_point_t *out);
/* The track's points appended after afterFrame (its follow-through). */
void l3_fit_span_after(const l3_club_track_t *track, uint32_t afterFrame, l3_fit_span_t *out);
/* One track: the last fitPoints of count for club in, the first fitPoints for
 * club out and ball out; out is fully written. */
void l3_impact_fit_track(const l3_impact_fit_cfg_t *cfg, uint8_t which, l3_point_at_fn pointAt,
                         const void *ctx, uint32_t count, float ballRangeM,
                         l3_fit_estimate_t *out);

#endif /* L3_IMPACT_FIT_H */
