/* See l3_band.h. */
#include <math.h>
#include <string.h>

#include "l3_band.h"

int32_t l3_band_contains(const l3_band_t *band, float bin)
{
    return (band->valid && bin >= band->loBin && bin <= band->hiBin) ? 1 : 0;
}

/* Compact targets in place, keeping those drop() rejects, in their order. */
static uint32_t l3_band_compact(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n,
                                int32_t (*drop)(const l3_band_t *band, float bin))
{
    uint32_t kept = 0U;
    uint32_t i;

    for (i = 0U; i < n; i++) {
        if (drop(band, targets[i].rangeBin)) {
            continue;
        }
        if (kept != i) {
            targets[kept] = targets[i];
        }
        kept++;
    }
    return kept;
}

/* 1 when bin is inside a valid band or beyond it. */
static int32_t l3_band_notShort(const l3_band_t *band, float bin)
{
    return (band->valid && bin >= band->loBin) ? 1 : 0;
}

uint32_t l3_band_filter(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n)
{
    return l3_band_compact(band, targets, n, l3_band_contains);
}

uint32_t l3_band_keep_short(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n)
{
    return l3_band_compact(band, targets, n, l3_band_notShort);
}

void l3_band_noise_reset(l3_band_noise_t *noise)
{
    memset(noise, 0, sizeof(*noise));
}

void l3_band_noise_update(l3_band_noise_t *noise, uint32_t stat, uint32_t firstBin,
                          const l3_bin_obs_t *obs, uint32_t count)
{
    uint32_t i;

    if (count > L3_BAND_NOISE_BINS) {
        count = L3_BAND_NOISE_BINS;
    }
    if (count == 0U) {
        return;
    }
    if (noise->updates == 0U || noise->firstBin != firstBin || noise->count != count) {
        noise->firstBin = firstBin;
        noise->count = count;
        noise->updates = 0U;
        for (i = 0U; i < count; i++) {
            noise->avg[i] = l3_obs_stat(stat, &obs[i]);
        }
    } else {
        for (i = 0U; i < count; i++) {
            float value = l3_obs_stat(stat, &obs[i]);

            noise->avg[i] += (value - noise->avg[i]) / (float)(1U << L3_BAND_NOISE_SHIFT);
        }
    }
    noise->updates++;
}

static void l3_band_span(int32_t lo, uint32_t width, l3_band_t *out)
{
    out->valid = 1U;
    out->loBin = (float)lo;
    out->hiBin = (float)(lo + (int32_t)width - 1);
}

void l3_band_place(const l3_band_noise_t *noise, float centreBin, float searchBins,
                   float widthBins, l3_band_t *out)
{
    uint32_t width;
    int32_t centre = (int32_t)floorf(centreBin + 0.5F);
    int32_t first;
    int32_t last;
    int32_t start;
    int32_t bestStart = 0;
    float bestSum = 0.0F;
    float bestGap = 0.0F;
    uint8_t found = 0U;

    memset(out, 0, sizeof(*out));
    if (!(widthBins >= 0.5F)) {
        return;
    }
    width = (uint32_t)(widthBins + 0.5F);
    first = (int32_t)ceilf(centreBin - searchBins);
    last = (int32_t)floorf(centreBin + searchBins) - (int32_t)width + 1;
    if (noise->updates >= L3_BAND_NOISE_MIN_UPDATES) {
        if (first < (int32_t)noise->firstBin) {
            first = (int32_t)noise->firstBin;
        }
        if (last > (int32_t)(noise->firstBin + noise->count) - (int32_t)width) {
            last = (int32_t)(noise->firstBin + noise->count) - (int32_t)width;
        }
        for (start = first; start <= last; start++) {
            float sum = 0.0F;
            float gap = fabsf((float)start + 0.5F * (float)(width - 1U) - centreBin);
            uint32_t k;

            for (k = 0U; k < width; k++) {
                sum += noise->avg[(uint32_t)start - noise->firstBin + k];
            }
            if (!found || sum > bestSum || (sum == bestSum && gap < bestGap)) {
                bestStart = start;
                bestSum = sum;
                bestGap = gap;
                found = 1U;
            }
        }
    }
    if (!found) {
        bestStart = centre - (int32_t)((width - 1U) / 2U);
    }
    l3_band_span(bestStart, width, out);
}
