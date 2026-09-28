/* See l3_band.h. */
#include <string.h>

#include "l3_band.h"

void l3_band_around(float centreBin, float halfWidthBins, l3_band_t *out)
{
    memset(out, 0, sizeof(*out));
    if (!(halfWidthBins > 0.0F)) {
        return;
    }
    out->valid = 1U;
    out->loBin = centreBin - halfWidthBins;
    out->hiBin = centreBin + halfWidthBins;
}

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
