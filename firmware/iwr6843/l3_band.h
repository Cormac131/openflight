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

/* [centre - halfWidth, centre + halfWidth]; halfWidth <= 0 disables it. */
void l3_band_around(float centreBin, float halfWidthBins, l3_band_t *out);
/* 1 when bin lies inside a valid band. */
int32_t l3_band_contains(const l3_band_t *band, float bin);
/* Drop the targets inside the band, keeping the others in their order
 * (strongest first). Returns how many are kept. */
uint32_t l3_band_filter(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n);

#endif /* L3_BAND_H */
