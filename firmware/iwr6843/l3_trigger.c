/* IWR6843 self-trigger front end. See l3_trigger.h for the design. Pure C,
 * no hardware: l3_dump.c feeds it observations; the club track fires. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_text.h"
#include "l3_trigger.h"

/* Bins are stored in a byte; the trace and the CLI never see one this high. */
#define L3_TRIG_BYTE_BINS 0xFFU

void l3_trig_cfg_defaults(l3_trig_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->approachBins = L3_TRIG_DEFAULT_APPROACH_BINS;
    cfg->pastBins = L3_TRIG_DEFAULT_PAST_BINS;
    cfg->stat = L3_TRIG_DEFAULT_STAT;
}

int32_t l3_trig_cfg_check(const l3_trig_cfg_t *cfg)
{
    if (!(cfg->snr >= 1.0F)) {
        return -1;
    }
    if (cfg->approachBins == 0U || cfg->approachBins > L3_TRIG_MAX_BINS) {
        return -1;
    }
    /* The region must reach further short of the destination than past it:
     * the club approaches from short of the ball. The trace stores bins in
     * a byte. */
    if (cfg->pastBins >= cfg->approachBins ||
        cfg->teeBin + cfg->pastBins >= L3_TRIG_BYTE_BINS) {
        return -1;
    }
    if (cfg->stat > L3_TRIG_STAT_PEAK) {
        return -1;
    }
    return 0;
}

/* The configured detection statistic of one observation. */
static float l3_trig_stat(const l3_trig_cfg_t *cfg, const l3_trig_obs_t *obs)
{
    return l3_obs_stat(cfg->stat, obs);
}

void l3_trig_init(l3_trig_t *trig, const l3_trig_cfg_t *cfg)
{
    memset(trig, 0, sizeof(*trig));
    trig->cfg = *cfg;
}

void l3_trig_trace_clear(l3_trig_t *trig)
{
    trig->traceQuiet = 0U;
    trig->traceNext = 0U;
    trig->traceCount = 0U;
    trig->maxBins = 0U;
    trig->maxFirstBin = 0U;
    memset(trig->maxStat, 0, sizeof(trig->maxStat));
    memset(trig->maxFrame, 0, sizeof(trig->maxFrame));
}

/* Every frame: the region's strongest bin into the max-hold, and into the
 * trace when it clears the trace bar. */
static void l3_trig_trace(l3_trig_t *trig, uint32_t frame, uint32_t teeBin, uint32_t firstBin,
                          const l3_trig_obs_t *obs, uint32_t count)
{
    const l3_trig_cfg_t *cfg = &trig->cfg;
    uint32_t strongest = 0U;
    uint32_t i;
    float strongestStat;

    if (trig->maxFirstBin != firstBin || trig->maxBins != count) {
        /* A different region (re-arm on another tee, another window). */
        trig->maxFirstBin = firstBin;
        trig->maxBins = count;
        memset(trig->maxStat, 0, sizeof(trig->maxStat));
        memset(trig->maxFrame, 0, sizeof(trig->maxFrame));
    }
    for (i = 0U; i < count; i++) {
        float stat = l3_trig_stat(cfg, &obs[i]);
        if (stat > trig->maxStat[i]) {
            trig->maxStat[i] = stat;
            trig->maxFrame[i] = frame;
        }
        if (stat > l3_trig_stat(cfg, &obs[strongest])) {
            strongest = i;
        }
    }
    strongestStat = l3_trig_stat(cfg, &obs[strongest]);
    if (strongestStat < L3_TRIG_TRACE_RATIO * trig->floor) {
        trig->traceQuiet++;
        return;
    }
    {
        l3_trig_trace_t *entry = &trig->trace[trig->traceNext];
        entry->frame = frame;
        entry->gap = (trig->traceQuiet > 0xFFFFU) ? 0xFFFFU : (uint16_t)trig->traceQuiet;
        entry->bin = (uint8_t)(firstBin + strongest);
        entry->dest = (uint8_t)teeBin;
        entry->energy = obs[strongest].energy;
        entry->peak = obs[strongest].peak;
        entry->loop0 = obs[strongest].loop0;
        entry->floor = trig->floor;
        entry->threshold = l3_trig_threshold(trig);
        entry->coherencePct = 0U;
        if (obs[strongest].energy > 0.0F) {
            float magnitude = sqrtf(obs[strongest].r1Re * obs[strongest].r1Re +
                                    obs[strongest].r1Im * obs[strongest].r1Im);
            float coherence = magnitude / obs[strongest].energy;
            if (coherence > 1.0F) {
                coherence = 1.0F;
            }
            entry->coherencePct = (uint8_t)(coherence * 100.0F + 0.5F);
        }
    }
    trig->traceNext = (trig->traceNext + 1U) % L3_TRIG_TRACE_DEPTH;
    if (trig->traceCount < L3_TRIG_TRACE_DEPTH) {
        trig->traceCount++;
    }
    trig->traceQuiet = 0U;
}

int32_t l3_trig_region(const l3_trig_cfg_t *cfg, uint32_t teeBin, uint32_t windowStart,
                       uint32_t binCount, uint32_t *firstLocal, uint32_t *count)
{
    uint32_t teeLocal;
    uint32_t first;
    uint32_t last;

    *firstLocal = 0U;
    *count = 0U;
    if (binCount == 0U || teeBin < windowStart || teeBin - windowStart >= binCount) {
        return 0;
    }
    teeLocal = teeBin - windowStart;
    first = (teeLocal > cfg->approachBins) ? (teeLocal - cfg->approachBins) : 0U;
    last = teeLocal + cfg->pastBins;
    if (last > binCount - 1U) {
        last = binCount - 1U;
    }
    *firstLocal = first;
    *count = last - first + 1U;
    if (*count > L3_TRIG_MAX_BINS) {
        *count = L3_TRIG_MAX_BINS;
    }
    return 1;
}

void l3_trig_observe(l3_trig_t *trig, uint32_t frame, uint32_t teeBin, uint32_t firstBin,
                     const l3_trig_obs_t *obs, uint32_t count)
{
    if (count == 0U) {
        return;
    }
    if (count > L3_TRIG_MAX_BINS) {
        count = L3_TRIG_MAX_BINS;
    }
    trig->frames++;
    /* Adaptive floor, owned by the observation layer: the median of the
     * region is noise even while the club occupies a few bins of it. */
    l3_obs_floor_update(&trig->floor, trig->cfg.stat, obs, count, L3_TRIG_FLOOR_SHIFT);
    l3_trig_trace(trig, frame, teeBin, firstBin, obs, count);
}

float l3_trig_threshold(const l3_trig_t *trig)
{
    return trig->floor * trig->cfg.snr;
}

uint32_t l3_trig_trace_count(const l3_trig_t *trig)
{
    return trig->traceCount;
}

int32_t l3_trig_trace_get(const l3_trig_t *trig, uint32_t index, l3_trig_trace_t *out)
{
    uint32_t oldest;

    if (index >= trig->traceCount) {
        return 0;
    }
    oldest = (trig->traceNext + L3_TRIG_TRACE_DEPTH - trig->traceCount) % L3_TRIG_TRACE_DEPTH;
    *out = trig->trace[(oldest + index) % L3_TRIG_TRACE_DEPTH];
    return 1;
}

static const char *l3_trig_stat_name(const l3_trig_cfg_t *cfg)
{
    return (cfg->stat == L3_TRIG_STAT_PEAK) ? "peak" : "energy";
}

int32_t l3_trig_format_summary(const l3_trig_t *trig, char *out, uint32_t cap)
{
    char floorText[16];
    char thresholdText[16];

    l3_text_fixed(trig->floor, 1U, floorText, sizeof(floorText));
    l3_text_fixed(l3_trig_threshold(trig), 1U, thresholdText, sizeof(thresholdText));
    return snprintf(out, cap, "trig frames=%u floor=%s thr=%s traced=%u",
                    (unsigned)trig->frames, floorText, thresholdText,
                    (unsigned)trig->traceCount);
}

int32_t l3_trig_format_config(const l3_trig_t *trig, char *out, uint32_t cap)
{
    char snrText[16];

    l3_text_fixed(trig->cfg.snr, 2U, snrText, sizeof(snrText));
    return snprintf(out, cap, "trigcfg tee=%u snr=%s approach=%u past=%u stat=%s",
                    (unsigned)trig->cfg.teeBin, snrText, (unsigned)trig->cfg.approachBins,
                    (unsigned)trig->cfg.pastBins, l3_trig_stat_name(&trig->cfg));
}

int32_t l3_trig_format_trace_header(const l3_trig_t *trig, char *out, uint32_t cap)
{
    char floorText[16];
    char ratioText[16];

    l3_text_fixed(trig->floor, 0U, floorText, sizeof(floorText));
    l3_text_fixed(L3_TRIG_TRACE_RATIO, 1U, ratioText, sizeof(ratioText));
    return snprintf(out, cap,
                    "trigtrace stat=%s floor=%s bar=%sx frames=%u region=%u+%u entries=%u",
                    l3_trig_stat_name(&trig->cfg), floorText, ratioText,
                    (unsigned)trig->frames, (unsigned)trig->maxFirstBin,
                    (unsigned)trig->maxBins, (unsigned)trig->traceCount);
}

/* One traced frame. e/f and p/f are energy and peak over the floor, which is
 * in the configured statistic's units, so the ratio for that statistic is
 * the one the threshold (thr = floor x snr) applies to. */
int32_t l3_trig_format_trace(const l3_trig_trace_t *entry, char *out, uint32_t cap)
{
    char energyText[16];
    char peakText[16];
    char loop0Text[16];
    char floorText[16];
    char thresholdText[16];
    char energyRatio[16];
    char peakRatio[16];
    float floor = (entry->floor > 0.0F) ? entry->floor : 1.0F;

    l3_text_fixed(entry->energy, 0U, energyText, sizeof(energyText));
    l3_text_fixed(entry->peak, 0U, peakText, sizeof(peakText));
    l3_text_fixed(entry->loop0, 0U, loop0Text, sizeof(loop0Text));
    l3_text_fixed(entry->floor, 0U, floorText, sizeof(floorText));
    l3_text_fixed(entry->threshold, 0U, thresholdText, sizeof(thresholdText));
    l3_text_fixed(entry->energy / floor, 1U, energyRatio, sizeof(energyRatio));
    l3_text_fixed(entry->peak / floor, 1U, peakRatio, sizeof(peakRatio));
    return snprintf(out, cap,
                    "t frame=%u gap=%u bin=%u dest=%u dist=%d energy=%s peak=%s "
                    "loop0=%s floor=%s thr=%s e/f=%s p/f=%s coh=%u",
                    (unsigned)entry->frame, (unsigned)entry->gap, (unsigned)entry->bin,
                    (unsigned)entry->dest, (int)entry->dest - (int)entry->bin,
                    energyText, peakText, loop0Text, floorText, thresholdText,
                    energyRatio, peakRatio, (unsigned)entry->coherencePct);
}

int32_t l3_trig_format_maxhold(const l3_trig_t *trig, uint32_t start, uint32_t count,
                               char *out, uint32_t cap)
{
    int32_t used = snprintf(out, cap, "trigmax");
    uint32_t i;

    for (i = start; i < start + count && i < trig->maxBins; i++) {
        char statText[16];
        int32_t written;

        if (used < 0 || (uint32_t)used >= cap) {
            break;
        }
        l3_text_fixed(trig->maxStat[i], 0U, statText, sizeof(statText));
        written = snprintf(out + used, cap - (uint32_t)used, " %u:%s@%u",
                           (unsigned)(trig->maxFirstBin + i), statText,
                           (unsigned)trig->maxFrame[i]);
        if (written < 0) {
            break;
        }
        used += written;
    }
    return used;
}
