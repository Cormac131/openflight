/* See l3_scan.h. */
#include <string.h>

#include "l3_scan.h"

void l3_scan_cfg_defaults(l3_scan_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->clubBins = 16U;       /* 20 kept 33/34 labelled swings firing, 16 32, 12 27 */
    cfg->leaveBins = 10U;      /* a median of 6, plus a 90 m/s step from 4 bins out */
    cfg->postBins = 12U;
    cfg->postBehindBins = 3U;
    cfg->postClubBins = 4U;
    cfg->mapChunkBins = 2U;
}

/* [first, last] (inclusive, signed) clipped to the window. */
static l3_span_t l3_scan_clip(int32_t first, int32_t last, uint32_t windowFirst,
                              uint32_t windowCount)
{
    l3_span_t out;
    int32_t lo = (int32_t)windowFirst;
    int32_t hi = (int32_t)(windowFirst + windowCount) - 1;

    if (first < lo) {
        first = lo;
    }
    if (last > hi) {
        last = hi;
    }
    out.first = (first >= 0) ? (uint32_t)first : 0U;
    out.count = (last >= first) ? (uint32_t)(last - first + 1) : 0U;
    return out;
}

static int32_t l3_scan_floor(float bin)
{
    int32_t whole = (int32_t)bin;

    return ((float)whole > bin) ? whole - 1 : whole;
}

void l3_scan_pre(const l3_scan_cfg_t *cfg, uint32_t windowFirst, uint32_t windowCount,
                 uint32_t regionFirst, uint32_t regionCount, const l3_band_t *band,
                 l3_span_t *region, l3_span_t *club, l3_span_t *leave)
{
    int32_t regionLast = (int32_t)(regionFirst + regionCount) - 1;
    int32_t lo;
    int32_t hi;

    if (band == NULL || !band->valid) {
        region->first = regionFirst;
        region->count = regionCount;
        *club = *region;
        leave->first = 0U;
        leave->count = 0U;
        return;
    }
    lo = l3_scan_floor(band->loBin);
    hi = l3_scan_floor(band->hiBin);
    *region = l3_scan_clip((int32_t)regionFirst, (regionLast < lo) ? regionLast : lo,
                           windowFirst, windowCount);
    if (region->count == 0U) {
        region->first = regionFirst;
    }
    *club = l3_scan_clip(lo - (int32_t)cfg->clubBins, lo, windowFirst, windowCount);
    *leave = l3_scan_clip(hi + 1, hi + (int32_t)cfg->leaveBins, windowFirst, windowCount);
}

void l3_scan_map_chunk(const l3_scan_cfg_t *cfg, uint32_t windowFirst, uint32_t windowCount,
                       const l3_band_t *band, uint32_t *cursor, l3_span_t *out)
{
    int32_t first;
    int32_t interior;
    int32_t take;

    out->first = 0U;
    out->count = 0U;
    if (band == NULL || !band->valid || cfg->mapChunkBins == 0U) {
        return;
    }
    first = l3_scan_floor(band->loBin) + 1;
    interior = l3_scan_floor(band->hiBin) - first + 1;
    if (interior <= 0) {
        return;
    }
    if (*cursor >= (uint32_t)interior) {
        *cursor = 0U;
    }
    take = interior - (int32_t)*cursor;
    if (take > (int32_t)cfg->mapChunkBins) {
        take = (int32_t)cfg->mapChunkBins;
    }
    *out = l3_scan_clip(first + (int32_t)*cursor, first + (int32_t)*cursor + take - 1,
                        windowFirst, windowCount);
    *cursor += (uint32_t)take;
}

/* bins from first, moved back so they end inside the window. */
static l3_span_t l3_scan_whole(int32_t first, uint32_t bins, uint32_t windowFirst,
                               uint32_t windowCount)
{
    int32_t lastStart = (int32_t)(windowFirst + windowCount) - (int32_t)bins;

    if (first > lastStart) {
        first = lastStart;
    }
    return l3_scan_clip(first, first + (int32_t)bins - 1, windowFirst, windowCount);
}

void l3_scan_post(const l3_scan_cfg_t *cfg, uint32_t windowFirst, uint32_t windowCount,
                  const l3_band_t *band, uint8_t ballTracking, float ballPredicted,
                  uint8_t clubTracking, float clubPredicted, l3_span_t *ball, l3_span_t *club)
{
    int32_t start = l3_scan_floor(band->hiBin) + 1;
    int32_t first = start;

    if (ballTracking) {
        first = l3_scan_floor(ballPredicted) - (int32_t)cfg->postBehindBins;
        if (first < start) {
            first = start;
        }
    }
    *ball = l3_scan_whole(first, cfg->postBins, windowFirst, windowCount);
    first = clubTracking ? l3_scan_floor(clubPredicted) - 1 : l3_scan_floor(band->hiBin) + 1;
    *club = l3_scan_whole(first, cfg->postClubBins, windowFirst, windowCount);
}

uint32_t l3_scan_merge(l3_span_t a, l3_span_t b, l3_span_t *out)
{
    l3_span_t lo;
    l3_span_t hi;

    if (a.count == 0U || b.count == 0U) {
        if (a.count == 0U && b.count == 0U) {
            return 0U;
        }
        out[0] = (a.count > 0U) ? a : b;
        return 1U;
    }
    lo = (a.first <= b.first) ? a : b;
    hi = (a.first <= b.first) ? b : a;
    if (hi.first <= lo.first + lo.count) {
        uint32_t end = lo.first + lo.count;

        if (hi.first + hi.count > end) {
            end = hi.first + hi.count;
        }
        out[0].first = lo.first;
        out[0].count = end - lo.first;
        return 1U;
    }
    out[0] = lo;
    out[1] = hi;
    return 2U;
}

uint32_t l3_scan_count(const l3_span_t *spans, uint32_t n)
{
    /* Spans are short and few: count each bin of each span that no earlier
     * span covers. */
    uint32_t total = 0U;
    uint32_t i;

    for (i = 0U; i < n; i++) {
        uint32_t bin;

        for (bin = spans[i].first; bin < spans[i].first + spans[i].count; bin++) {
            uint32_t j;
            uint8_t seen = 0U;

            for (j = 0U; j < i && !seen; j++) {
                seen = (uint8_t)(bin >= spans[j].first && bin < spans[j].first + spans[j].count);
            }
            total += seen ? 0U : 1U;
        }
    }
    return total;
}
