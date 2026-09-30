/* IWR6843 detect timing. See l3_timing.h. */
#include "l3_timing.h"

#include <limits.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "l3_detect_core.h"

#define L3_TIMING_NO_MARGIN (INT32_MIN / 2)

void l3_timing_init(l3_timing_t *timing, uint32_t ticksPerUs, uint32_t budgetUs,
                    uint32_t ringFrames)
{
    memset(timing, 0, sizeof(*timing));
    timing->ticksPerUs = (ticksPerUs == 0U) ? 1U : ticksPerUs;
    timing->budgetUs = budgetUs;
    timing->ringFrames = ringFrames;
    l3_timing_reset(timing);
}

void l3_timing_reset(l3_timing_t *timing)
{
    uint32_t ticksPerUs = timing->ticksPerUs;
    uint32_t budgetUs = timing->budgetUs;
    uint32_t ringFrames = timing->ringFrames;

    memset(timing, 0, sizeof(*timing));
    timing->ticksPerUs = ticksPerUs;
    timing->budgetUs = budgetUs;
    timing->ringFrames = ringFrames;
    timing->marginMinUs = INT32_MAX;
}

static uint32_t l3_timing_us(const l3_timing_t *timing, uint32_t from, uint32_t to)
{
    return (to - from) / timing->ticksPerUs; /* unsigned: survives the wrap */
}

static void l3_timing_add(l3_timing_stat_t *stat, uint32_t us)
{
    if (stat->count == 0U || us < stat->minUs) {
        stat->minUs = us;
    }
    if (us > stat->maxUs) {
        stat->maxUs = us;
    }
    stat->lastUs = us;
    stat->count++;
    if (stat->sumUs > UINT32_MAX - us) {
        stat->sumUs = UINT32_MAX;
        stat->sumOverflow = 1U;
    } else {
        stat->sumUs += us;
    }
}

int32_t l3_timing_margin_us(const l3_timing_t *timing, uint32_t latencyUs)
{
    uint64_t life;

    if (timing->ringFrames < 2U) {
        return L3_TIMING_NO_MARGIN;
    }
    /* Two 32-bit factors: the product fits 64 unsigned bits, never signed. */
    life = (uint64_t)(timing->ringFrames - 2U) * (uint64_t)timing->budgetUs;
    if (life >= (uint64_t)INT32_MAX + (uint64_t)latencyUs) {
        return INT32_MAX;
    }
    if ((uint64_t)latencyUs > life + (uint64_t)(-(int64_t)L3_TIMING_NO_MARGIN)) {
        return L3_TIMING_NO_MARGIN;
    }
    return (int32_t)((int64_t)life - (int64_t)latencyUs);
}

void l3_timing_record(l3_timing_t *timing, const l3_timing_event_t *event)
{
    uint32_t latency = l3_timing_us(timing, event->acquired, event->decided);
    uint32_t service = l3_timing_us(timing, event->dequeued, event->decided);

    timing->frames++;
    l3_timing_add(&timing->stat[L3_TIMING_WAIT],
                  l3_timing_us(timing, event->acquired, event->dequeued));
    if ((event->flags & L3_TIMING_FLAG_SCORED) != 0U) {
        l3_timing_add(&timing->stat[L3_TIMING_SCORE],
                      l3_timing_us(timing, event->scoreStart, event->scoreEnd));
    }
    l3_timing_add(&timing->stat[L3_TIMING_SERVICE], service);
    l3_timing_add(&timing->stat[L3_TIMING_LATENCY], latency);
    if (service > timing->budgetUs) {
        timing->overBudget++;
    }
    if (event->depth > timing->depthMax) {
        timing->depthMax = event->depth;
    }
    if ((event->flags & L3_TIMING_FLAG_POST) == 0U) {
        int32_t margin = l3_timing_margin_us(timing, latency);

        if (timing->ringFrames >= 2U) {
            timing->marginCount++;
            timing->marginLastUs = margin;
            if (margin < timing->marginMinUs) {
                timing->marginMinUs = margin;
            }
            if (margin < 0) {
                timing->marginNegative++;
            }
        }
        if (timing->havePrevious && event->epoch == timing->previousEpoch + 1U) {
            l3_timing_add(&timing->stat[L3_TIMING_ARRIVAL],
                          l3_timing_us(timing, timing->previousAcquired, event->acquired));
        }
        timing->havePrevious = 1U;
        timing->previousEpoch = event->epoch;
        timing->previousAcquired = event->acquired;
    }
    timing->timeline[timing->timelineNext] = *event;
    timing->timelineNext = (timing->timelineNext + 1U) % L3_TIMING_TIMELINE_DEPTH;
    if (timing->timelineCount < L3_TIMING_TIMELINE_DEPTH) {
        timing->timelineCount++;
    }
}

uint32_t l3_timing_mean_us(const l3_timing_t *timing, uint32_t stat)
{
    if (stat >= L3_TIMING_STAT_COUNT || timing->stat[stat].count == 0U) {
        return 0U;
    }
    return timing->stat[stat].sumUs / timing->stat[stat].count;
}

static const char *const kStatNames[L3_TIMING_STAT_COUNT] = {
    "wait", "score", "service", "latency", "arrival"
};

const char *l3_timing_stat_name(uint32_t stat)
{
    return (stat < L3_TIMING_STAT_COUNT) ? kStatNames[stat] : "?";
}

int32_t l3_timing_event(const l3_timing_t *timing, uint32_t index, l3_timing_event_t *out)
{
    uint32_t oldest;

    if (index >= timing->timelineCount || out == NULL) {
        return -1;
    }
    oldest = (timing->timelineCount < L3_TIMING_TIMELINE_DEPTH) ? 0U : timing->timelineNext;
    *out = timing->timeline[(oldest + index) % L3_TIMING_TIMELINE_DEPTH];
    return 0;
}

int32_t l3_timing_format_summary(const l3_timing_t *timing, char *out, uint32_t cap)
{
    if (timing->marginCount == 0U) {
        return (int32_t)snprintf(out, cap,
                                 "timing frames=%u budget_us=%u over_budget=%u depth_max=%u "
                                 "ring=%u margin_last_us=- margin_min_us=- margin_negative=0",
                                 (unsigned)timing->frames, (unsigned)timing->budgetUs,
                                 (unsigned)timing->overBudget, (unsigned)timing->depthMax,
                                 (unsigned)timing->ringFrames);
    }
    return (int32_t)snprintf(out, cap,
                             "timing frames=%u budget_us=%u over_budget=%u depth_max=%u "
                             "ring=%u margin_last_us=%d margin_min_us=%d margin_negative=%u",
                             (unsigned)timing->frames, (unsigned)timing->budgetUs,
                             (unsigned)timing->overBudget, (unsigned)timing->depthMax,
                             (unsigned)timing->ringFrames, (int)timing->marginLastUs,
                             (int)timing->marginMinUs, (unsigned)timing->marginNegative);
}

int32_t l3_timing_format_stat(const l3_timing_t *timing, uint32_t stat, char *out, uint32_t cap)
{
    const l3_timing_stat_t *s;

    if (stat >= L3_TIMING_STAT_COUNT) {
        return (int32_t)snprintf(out, cap, "timing ? n=0");
    }
    s = &timing->stat[stat];
    return (int32_t)snprintf(out, cap, "timing %s n=%u last=%u min=%u mean=%u max=%u",
                             l3_timing_stat_name(stat), (unsigned)s->count,
                             (unsigned)s->lastUs, (unsigned)s->minUs,
                             (unsigned)l3_timing_mean_us(timing, stat), (unsigned)s->maxUs);
}

static const char *l3_timing_core_name(uint8_t core)
{
    const char *name;

    if (core == L3_TIMING_CORE_FALLBACK) {
        return "fallback";
    }
    name = l3_detect_core_name(core);
    return (name != NULL) ? name : "?";
}

int32_t l3_timing_format_event(const l3_timing_t *timing, const l3_timing_event_t *event,
                               char *out, uint32_t cap)
{
    static const struct {
        uint8_t bit;
        const char *name;
    } kFlags[] = {
        { L3_TIMING_FLAG_POST, "post" },
        { L3_TIMING_FLAG_BEHIND, "behind" },
        { L3_TIMING_FLAG_STALE, "stale" },
        { L3_TIMING_FLAG_FIRED, "fired" },
    };
    char flags[40];
    char epoch[12];
    char score[12];
    uint32_t used = 0U;
    uint32_t k;

    flags[0] = '\0';
    for (k = 0U; k < sizeof(kFlags) / sizeof(kFlags[0]); k++) {
        if ((event->flags & kFlags[k].bit) != 0U) {
            used += (uint32_t)snprintf(&flags[used], sizeof(flags) - used, "%s%s",
                                       used > 0U ? "|" : "", kFlags[k].name);
        }
    }
    if (used == 0U) {
        (void)snprintf(flags, sizeof(flags), "-");
    }
    if ((event->flags & L3_TIMING_FLAG_POST) != 0U) {
        (void)snprintf(epoch, sizeof(epoch), "post");
    } else {
        (void)snprintf(epoch, sizeof(epoch), "%u", (unsigned)event->epoch);
    }
    if ((event->flags & L3_TIMING_FLAG_SCORED) != 0U) {
        (void)snprintf(score, sizeof(score), "%u",
                       (unsigned)l3_timing_us(timing, event->scoreStart, event->scoreEnd));
    } else {
        (void)snprintf(score, sizeof(score), "-");
    }
    return (int32_t)snprintf(out, cap,
                             "timeline slot=%u epoch=%s core=%s wait_us=%u score_us=%s "
                             "service_us=%u latency_us=%u depth=%u flags=%s",
                             (unsigned)event->slot, epoch, l3_timing_core_name(event->core),
                             (unsigned)l3_timing_us(timing, event->acquired, event->dequeued),
                             score,
                             (unsigned)l3_timing_us(timing, event->dequeued, event->decided),
                             (unsigned)l3_timing_us(timing, event->acquired, event->decided),
                             (unsigned)event->depth, flags);
}
