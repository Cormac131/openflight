/* IWR6843 tee band: the bins around the ball where the MTI map carries a
 * ridge for the whole capture, so impact cannot be read there. Targets inside
 * it are dropped before any tracker sees them; the impact is established from
 * the tracks either side (l3_impact_fit.h). Pure C, no hardware.
 */
#ifndef L3_BAND_H
#define L3_BAND_H

#include <stdint.h>

#include "l3_observation.h"

typedef struct {
    uint8_t valid;    /* 0: no band, nothing is inside */
    float   loBin;    /* global bins, both edges inside */
    float   hiBin;
} l3_band_t;

#define L3_BAND_NOISE_BINS        64U
#define L3_BAND_NOISE_SHIFT       4U    /* EMA constant 1/16 */
#define L3_BAND_NOISE_MIN_UPDATES 8U

typedef struct {
    uint32_t firstBin;                 /* global bin of avg[0] */
    uint32_t count;                    /* bins covered, 0 before the first update */
    uint32_t updates;
    float    avg[L3_BAND_NOISE_BINS];
    uint8_t  seen[L3_BAND_NOISE_BINS]; /* updates per bin, saturating: history for
                                        * l3_band_place when fed span by span */
} l3_band_noise_t;

void l3_band_noise_reset(l3_band_noise_t *noise);
/* One idle frame's observations over global bins [firstBin, firstBin + count):
 * EMA of l3_obs_stat(stat, ...); a different window restarts the map. */
void l3_band_noise_update(l3_band_noise_t *noise, uint32_t stat, uint32_t firstBin,
                          const l3_bin_obs_t *obs, uint32_t count);
/* An idle frame's scored span, global bins [spanFirst, spanFirst + count),
 * obs its observations, into a map kept over the window [windowFirst,
 * windowFirst + windowCount) (l3_scan.h: an armed frame scores only some
 * bins). A different window restarts the map; bins outside it are ignored;
 * a bin's first update seeds it. */
void l3_band_noise_update_span(l3_band_noise_t *noise, uint32_t stat, uint32_t windowFirst,
                               uint32_t windowCount, uint32_t spanFirst, const l3_bin_obs_t *obs,
                               uint32_t count);
/* The contiguous run of round(widthBins) bins with the largest summed noise,
 * inside [centre - searchBins, centre + searchBins] and the map, that holds
 * round(centreBin) (the ball's bin is always inside the band: a ridge wholly
 * to one side puts the band at that side's edge); ties nearest the centre.
 * Centred on round(centreBin) without enough history or room.
 * widthBins < 0.5 gives an invalid band. */
void l3_band_place(const l3_band_noise_t *noise, float centreBin, float searchBins,
                   float widthBins, l3_band_t *out);

/* 1 when bin lies inside a valid band. */
int32_t l3_band_contains(const l3_band_t *band, float bin);
/* Drop the targets inside the band, keeping the others in their order
 * (strongest first). Returns how many are kept. */
uint32_t l3_band_filter(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n);
/* Keep only the targets short of the band (rangeBin < loBin), in their order:
 * before impact the club approaches the ball, so nothing in the band or
 * beyond it can be the club. An invalid band keeps everything. Returns how
 * many are kept. */
uint32_t l3_band_keep_short(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n);

#endif /* L3_BAND_H */
