/* IWR6843 ball-placement detector: a new, compact, stable reflector against a
 * slowly learned static background.
 *
 * The trigger's MTI residual removes a stationary ball entirely, and the
 * strongest static reflector in the lane is usually furniture (ten captures
 * put one at about 2.06 m), so neither "motion" nor "biggest static return"
 * finds the ball. What does is the one thing the golfer always does: place
 * it. This module keeps a per-bin background of the static power while the
 * tee is empty, and reports the bin where a compact reflector appeared and
 * stayed. Removal after a shot returns it to waiting, so the driving-range
 * loop (place, swing, place) needs no button.
 *
 * Bins are GLOBAL range-FFT bins. Pure C, no hardware: l3_dump.c feeds it
 * the static power of the pre window every few frames and tests drive it
 * with the host compiler.
 */
#ifndef L3_BALL_H
#define L3_BALL_H

#include <stdint.h>

#define L3_BALL_MAX_BINS 64U
/* Bins either side of a locked ball whose background is frozen while it sits
 * there, so the ball is not learned into the background. */
#define L3_BALL_HOLD_BINS 2U
/* Fast smoothing of the current profile: 1/4 per update. */
#define L3_BALL_CURRENT_SHIFT 2U
/* Slow background learning: 1/256 per update. */
#define L3_BALL_BACKGROUND_SHIFT 8U
/* Static power under this is treated as this, so ratios stay finite. */
#define L3_BALL_POWER_MIN 1.0F
/* Updates of history behind the persistence figure (a 64-bit mask). */
#define L3_BALL_HISTORY 50U
/* Contrast counts as full confidence from this ratio up. */
#define L3_BALL_FULL_RATIO 4.0F

/* Why the last update did not lock (or hold) a ball. */
enum {
    L3_BALL_REASON_NONE = 0,      /* locked, or building */
    L3_BALL_REASON_NO_DELTA,      /* no bin rose to minRatio over its background */
    L3_BALL_REASON_TOO_WIDE,      /* the rise spans too many bins to be a ball */
    L3_BALL_REASON_UNSTABLE,      /* a candidate moved or vanished before stableUpdates */
    L3_BALL_REASON_GONE,          /* a locked ball's return fell away */
    L3_BALL_REASON_COUNT
};

enum {
    L3_BALL_STATE_OFF = 0,       /* not configured */
    L3_BALL_STATE_BUILDING = 1,  /* learning the empty background */
    L3_BALL_STATE_WAITING = 2,   /* background known, no ball */
    L3_BALL_STATE_CANDIDATE = 3, /* a new compact reflector, not yet stable */
    L3_BALL_STATE_LOCKED = 4     /* ball on the tee at ballBin */
};

typedef struct {
    uint8_t  enabled;
    uint8_t  follow;          /* the trigger's destination is the locked ball */
    float    minRatio;        /* (current - background) / background to be a candidate */
    uint32_t buildUpdates;    /* updates before the background counts as learned */
    uint32_t stableUpdates;   /* candidate updates in place before locking */
    float    goneFraction;    /* ball delta below this fraction of the lock = gone */
    uint32_t goneUpdates;     /* consecutive gone updates before releasing */
} l3_ball_cfg_t;

typedef struct {
    l3_ball_cfg_t cfg;
    uint8_t  state;
    uint8_t  reason;          /* L3_BALL_REASON_* from the last update */
    uint32_t updates;         /* since init or the last region change */
    uint32_t windowStartBin;  /* global bin of background[0] */
    uint32_t count;
    float    background[L3_BALL_MAX_BINS];
    float    current[L3_BALL_MAX_BINS];
    /* Candidate / locked reflector, all in global bins. */
    uint32_t candidateBin;
    uint32_t candidateAge;
    float    centroid;        /* delta-weighted over the cluster, sub-bin */
    uint32_t width;           /* contiguous bins sharing the rise */
    uint32_t ballBin;         /* valid when LOCKED; frozen until release */
    float    ballCentroid;    /* centroid at lock */
    float    ballDelta;       /* settled current - background */
    float    ballBackground;
    uint32_t ballAge;         /* updates since lock */
    uint32_t goneAge;
    uint64_t history;         /* one bit per update: a ball was seen */
    uint32_t locks;
    uint32_t releases;
    uint32_t reasons[L3_BALL_REASON_COUNT];
} l3_ball_t;

void l3_ball_cfg_defaults(l3_ball_cfg_t *cfg);
int32_t l3_ball_cfg_check(const l3_ball_cfg_t *cfg);
void l3_ball_init(l3_ball_t *ball, const l3_ball_cfg_t *cfg);
/* One update: power[i] is the static power of global bin firstBin + i.
 * Returns the state afterwards. */
uint8_t l3_ball_update(l3_ball_t *ball, uint32_t firstBin, const float *power, uint32_t count);
/* 1 and the ball's global bin while LOCKED, else 0. */
int32_t l3_ball_locked(const l3_ball_t *ball, uint32_t *bin);
/* Ratio of the locked ball's delta to its background, 0 when not locked. */
float l3_ball_ratio(const l3_ball_t *ball);
/* Fraction of the last L3_BALL_HISTORY updates that saw a ball, 0..1. */
float l3_ball_persistence(const l3_ball_t *ball);
/* 0..1 from contrast, width, persistence and range stability; 0 unless locked. */
float l3_ball_confidence(const l3_ball_t *ball);
const char *l3_ball_state_name(uint8_t state);
const char *l3_ball_reason_name(uint8_t reason);
/* "ball state=locked follow=1 bin=48 ratio=8.65 ..." (one line) */
int32_t l3_ball_format_status(const l3_ball_t *ball, char *out, uint32_t cap);
/* "balldbg centroid=48.27 width=2 persistence=47/50 confidence=0.93 ..." */
int32_t l3_ball_format_debug(const l3_ball_t *ball, char *out, uint32_t cap);

#endif /* L3_BALL_H */
