/* IWR6843 retention policy: which IQ16 bins a frame keeps in L3.
 *
 * The HWA produces a wide range window every frame (the PROCESSING region:
 * the noise floor, background, candidate discovery and association need
 * context) and the detect task reads all of it at full IQ16 precision. What
 * goes into the L3 ring (the RETENTION region) is narrower: the shot state
 * machine, the club track and the ball track know where the action is, and
 * this module turns that knowledge into a window start for each phase's
 * fixed slot width, a retention priority for the frame and a reason code
 * saying which rule chose it:
 *
 *   waiting / ready         around the locked ball (or the configured tee)
 *   club acquired / tracked around the predicted club bin
 *   club near the ball      from min(club, ball) - margin to max + margin
 *   impact frames           around the ball, biased toward the club
 *   ball search             from the origin outward (the flight moves away)
 *   ball confirmed          ahead of the predicted flight position
 *
 * every start clipped so the window stays inside the processing region.
 * The slot widths themselves are the capture plan's per-phase retain widths
 * (l3_retain_budget spends L3 on them in priority order: impact frames
 * first, then the first ball frames, then the last club frames), so the
 * ring layout stays fixed per shot and only where each frame looks moves.
 * l3_frame_desc_t records what each stored frame is. Pure C, no hardware.
 */
#ifndef L3_RETAIN_H
#define L3_RETAIN_H

#include <stdint.h>

/* Retention priorities, lowest first: what a constrained budget drops first. */
enum {
    L3_RETAIN_LOW = 0,      /* generic history around a still scene */
    L3_RETAIN_TRACK,        /* the club approaching */
    L3_RETAIN_BALL,         /* the ball in flight */
    L3_RETAIN_IMPACT,       /* the club at the ball, before and after t0 */
    L3_RETAIN_SPIN,         /* the first flight frames, kept for the spin research */
    L3_RETAIN_PRIORITY_COUNT
};

/* Which rule chose a window. */
enum {
    L3_RETAIN_WHY_CENTRED = 0,  /* policy disabled: centred in the processing region */
    L3_RETAIN_WHY_TEE,          /* no ball locked: around the configured tee */
    L3_RETAIN_WHY_BALL,         /* ball locked, no club: around the ball */
    L3_RETAIN_WHY_CLUB,         /* around the predicted club */
    L3_RETAIN_WHY_APPROACH,     /* club within reach of the ball: span both */
    L3_RETAIN_WHY_IMPACT,       /* impact frames around the ball */
    L3_RETAIN_WHY_BALL_SEARCH,  /* post impact, ball not confirmed: origin outward */
    L3_RETAIN_WHY_BALL_FOLLOW,  /* confirmed flight: ahead of the prediction */
    L3_RETAIN_WHY_COUNT
};

typedef struct {
    uint8_t processStart;   /* global bin the HWA window starts at */
    uint8_t processBins;
    uint8_t retainStart;    /* global bin the stored window starts at */
    uint8_t retainBins;     /* <= processBins; == processBins keeps everything */
} l3_roi_t;

typedef struct {
    uint8_t enabled;            /* 0: centred windows (compact16); 1: state-aware (adaptive16) */
    uint8_t approachBins;       /* club within this many bins of the ball: span both */
    uint8_t approachMarginBins; /* ... with this margin either side */
    uint8_t impactBiasBins;     /* impact windows sit this many bins short of the ball */
    uint8_t ballSearchLeadBins; /* ball search windows start this many bins short of the origin */
    uint8_t ballFollowLeadBins; /* confirmed flight windows start this far behind the prediction */
    uint8_t spinFrames;         /* first post-impact frames tagged L3_RETAIN_SPIN */
} l3_retain_cfg_t;

/* What the policy needs to know about the coming frame. Bins are global. */
typedef struct {
    uint8_t shotState;          /* L3_SHOT_* */
    uint8_t ballLocked;
    float   ballBin;            /* locked ball, or the configured tee when not locked */
    uint8_t clubActive;
    float   clubBin;            /* predicted club bin for the coming frame */
    uint8_t postFrame;          /* 1 once impact froze the pre ring */
    uint32_t postIndex;         /* 0-based index of the coming post frame */
    uint8_t ballTrackConfirmed;
    float   ballTrackBin;       /* predicted flight position for the coming frame */
} l3_retain_state_t;

typedef struct {
    uint8_t start;      /* global bin */
    uint8_t bins;
    uint8_t priority;   /* L3_RETAIN_* */
    uint8_t why;        /* L3_RETAIN_WHY_* */
} l3_retain_window_t;

/* One stored frame: what the ring holds and why. */
typedef struct {
    uint32_t timestampUs;
    uint32_t dataOffset;    /* byte offset into the ring */
    uint32_t bytes;
    uint16_t frame;         /* capture frame number */
    uint8_t  globalBinStart;
    uint8_t  binCount;
    uint8_t  processStart;  /* the wider region the detect task saw */
    uint8_t  processBins;
    uint8_t  shotState;
    uint8_t  priority;
    uint8_t  why;
    uint8_t  isPost;
} l3_frame_desc_t;

/* Requested and granted frame counts for l3_retain_budget. */
typedef struct {
    uint32_t bytesPerBin;       /* chirps x rx x bytes per complex */
    uint32_t capacityBytes;
    uint32_t maxFrames;         /* descriptor table size */
    uint8_t  preBins;
    uint8_t  impactBins;
    uint8_t  ballBins;
    uint8_t  preFrames;         /* wanted */
    uint8_t  impactFrames;      /* wanted; never cut */
    uint8_t  ballFrames;        /* wanted */
} l3_retain_request_t;

typedef struct {
    uint8_t  preFrames;
    uint8_t  impactFrames;
    uint8_t  ballFrames;
    uint8_t  cutPre;            /* frames the budget took from the request */
    uint8_t  cutBall;
    uint32_t usedBytes;
    uint32_t freeBytes;
} l3_retain_budget_t;

void l3_retain_cfg_defaults(l3_retain_cfg_t *cfg);
int32_t l3_retain_cfg_check(const l3_retain_cfg_t *cfg);
/* A track's prediction for the next frame from its last point and rate. */
float l3_retain_predict(float lastBin, float velocityBinsPerFrame);
/* The window `retainBins` wide the coming frame keeps, inside the processing
 * region [processStart, processStart + processBins). Never fails: a window
 * wider than the region is clipped to the region. */
void l3_retain_window(const l3_retain_cfg_t *cfg, const l3_retain_state_t *state,
                      uint32_t processStart, uint32_t processBins, uint32_t retainBins,
                      l3_retain_window_t *out);
/* Fill an ROI from a window. */
void l3_retain_roi(uint32_t processStart, uint32_t processBins, const l3_retain_window_t *window,
                   l3_roi_t *roi);
/* Spend the capacity in priority order. Returns 0, or -1 when even the impact
 * frames (plus one pre and one ball frame) do not fit. */
int32_t l3_retain_budget(const l3_retain_request_t *request, l3_retain_budget_t *out);
const char *l3_retain_priority_name(uint8_t priority);
const char *l3_retain_why_name(uint8_t why);
/* "retain start=40 bins=16 prio=track why=club" */
int32_t l3_retain_format(const l3_retain_window_t *window, char *out, uint32_t cap);
/* "budget pre=9 impact=7 ball=8 cut=0/0 used=123456 free=789" */
int32_t l3_retain_format_budget(const l3_retain_budget_t *budget, char *out, uint32_t cap);
/* "frame 12 t=36000us bins 40+16 of 20+53 state=club_track prio=track why=club post=0" */
int32_t l3_frame_desc_format(const l3_frame_desc_t *desc, char *out, uint32_t cap);

#endif /* L3_RETAIN_H */
