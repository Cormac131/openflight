/* IWR6843 detect timing: latency and throughput, kept apart.
 *
 * l3_profile.h says what each detect stage costs. This says when: every
 * frame the detect task finishes carries the cycle stamps of its life
 *
 *   acquired     the HWA/EDMA finished storing it and it was queued
 *   dequeued     the detect task took it
 *   scoreStart   its bins' scoring began (either core, l3_detect_core.h)
 *   scoreEnd     and ended
 *   decided      the task was done with it: the trigger decided, the slot
 *                released
 *
 * and from them two different deadlines are measured, because they are
 * different things:
 *
 *   throughput   service (decided - dequeued) must average below the frame
 *                interval, budgetUs (3000 us), or the queue grows without
 *                bound; overBudget counts the frames whose service did not
 *   latency      decided - acquired may exceed the interval for a frame as
 *                long as the frame's ring slot has not been reused: the
 *                reuse margin, (ringFrames - 2) intervals after acquired
 *                (when the writer starts on the frame before it), minus the
 *                latency. Negative margins are counted and clamped.
 *
 * arrival is the interval between consecutive pre-impact frames' acquired
 * stamps (consecutive epochs only: a dropped or stale frame in between would
 * read as a long interval). Post-impact frames are never reused while the
 * ring is frozen: no margin, no arrival. Stamps are 32-bit cycle counts;
 * differences are taken unsigned, so they survive the counter wrapping (at
 * 200 MHz every ~21 s) as long as one frame's life is shorter than a wrap.
 * The last L3_TIMING_TIMELINE_DEPTH frames are kept whole for
 * "triggerLog timing". Pure C, no hardware.
 */
#ifndef L3_TIMING_H
#define L3_TIMING_H

#include <stdint.h>

#define L3_TIMING_TIMELINE_DEPTH 16U

/* l3_timing_event_t.flags */
#define L3_TIMING_FLAG_POST   1U /* a post-impact frame (the ball tracker's) */
#define L3_TIMING_FLAG_BEHIND 2U /* a newer frame landed before this was taken */
#define L3_TIMING_FLAG_STALE  4U /* its slot was reused while it was read */
#define L3_TIMING_FLAG_FIRED  8U /* the self-trigger fired on it */
#define L3_TIMING_FLAG_SCORED 16U /* scoreStart/scoreEnd are set */

/* l3_timing_event_t.core: the route (l3_detect_core.h), or FALLBACK when a
 * dss frame was scored on the MSS after the DSS failed it. */
#define L3_TIMING_CORE_FALLBACK 3U

typedef struct {
    uint32_t slot;
    uint32_t epoch;
    uint32_t acquired;
    uint32_t dequeued;
    uint32_t scoreStart;
    uint32_t scoreEnd;
    uint32_t decided;
    uint8_t  core;
    uint8_t  flags;
    uint8_t  depth;       /* queue items waiting when this one was taken */
    uint8_t  reserved;
} l3_timing_event_t;

typedef struct {
    uint32_t count;
    uint32_t lastUs;
    uint32_t minUs;
    uint32_t maxUs;
    uint32_t sumUs;       /* saturates rather than wrapping */
    uint32_t sumOverflow; /* set once sumUs saturated; mean is then a floor */
} l3_timing_stat_t;

enum {
    L3_TIMING_WAIT = 0,   /* dequeued - acquired */
    L3_TIMING_SCORE,      /* scoreEnd - scoreStart */
    L3_TIMING_SERVICE,    /* decided - dequeued */
    L3_TIMING_LATENCY,    /* decided - acquired */
    L3_TIMING_ARRIVAL,    /* acquired - the previous consecutive frame's */
    L3_TIMING_STAT_COUNT
};

typedef struct {
    uint32_t ticksPerUs;
    uint32_t budgetUs;     /* the frame interval */
    uint32_t ringFrames;   /* pre-impact ring slots */
    uint32_t frames;       /* events recorded */
    uint32_t overBudget;   /* service > budgetUs */
    uint32_t depthMax;
    uint32_t marginCount;  /* pre frames with a margin */
    int32_t  marginLastUs;
    int32_t  marginMinUs;
    uint32_t marginNegative;
    uint8_t  havePrevious;
    uint32_t previousEpoch;
    uint32_t previousAcquired;
    l3_timing_stat_t stat[L3_TIMING_STAT_COUNT];
    uint32_t timelineNext;
    uint32_t timelineCount;
    l3_timing_event_t timeline[L3_TIMING_TIMELINE_DEPTH];
} l3_timing_t;

/* ticksPerUs of 0 is taken as 1. Everything recorded is cleared. */
void l3_timing_init(l3_timing_t *timing, uint32_t ticksPerUs, uint32_t budgetUs,
                    uint32_t ringFrames);
/* Clear what was recorded; keep the clock, budget and ring. */
void l3_timing_reset(l3_timing_t *timing);
/* One finished frame. A stat whose stamps are missing (score without the
 * SCORED flag) is not recorded. */
void l3_timing_record(l3_timing_t *timing, const l3_timing_event_t *event);
/* The reuse margin of a pre-impact frame whose latency is latencyUs:
 * (ringFrames - 2) * budgetUs - latencyUs, as a signed value; a ring of
 * fewer than 2 slots has none (INT32_MIN / 2, far below any margin). */
int32_t l3_timing_margin_us(const l3_timing_t *timing, uint32_t latencyUs);
uint32_t l3_timing_mean_us(const l3_timing_t *timing, uint32_t stat);
const char *l3_timing_stat_name(uint32_t stat);
/* The recorded events, oldest first: index 0 .. timelineCount - 1. 0, or
 * -1 when there is no such event. */
int32_t l3_timing_event(const l3_timing_t *timing, uint32_t index, l3_timing_event_t *out);

/* "timing frames=N budget_us=3000 over_budget=N depth_max=N ring=R
 *  margin_last_us=M margin_min_us=M margin_negative=N" (the margins read
 *  "-" before any pre-impact frame) */
int32_t l3_timing_format_summary(const l3_timing_t *timing, char *out, uint32_t cap);
/* "timing latency n=N last=L min=M mean=A max=X" (microseconds) */
int32_t l3_timing_format_stat(const l3_timing_t *timing, uint32_t stat, char *out, uint32_t cap);
/* "timeline slot=S epoch=E core=dss wait_us=W score_us=C service_us=V
 *  latency_us=L depth=D flags=post|behind|stale|fired" (flags "-" for
 *  none; score_us "-" when not scored; epoch "post" for a post frame) */
int32_t l3_timing_format_event(const l3_timing_t *timing, const l3_timing_event_t *event,
                               char *out, uint32_t cap);

#endif /* L3_TIMING_H */
