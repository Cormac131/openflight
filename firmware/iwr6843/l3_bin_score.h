/* IWR6843 per-bin scoring: one range bin's burst-MTI observation from an
 * IQ16 frame.
 *
 * What l3_verticalResidual computes for IQ16 frames, pulled out so the MSS
 * (R4F) and the DSS (C674x) run the same code and their answers agree bit
 * for bit. Sums the vertical TX pair (every TX but the azimuth element, TX1
 * of a three-TX loop) over every RX, in exact integers (l3_iq16_stats.c).
 * Pure C, no hardware.
 *
 * Frame layout, as the ring stores it: int16 words
 * [loop][tx][rx][bin][Im, Re].
 */
#ifndef L3_BIN_SCORE_H
#define L3_BIN_SCORE_H

#include <stdint.h>

#include "l3_observation.h"

#define L3_BIN_SCORE_MAX_TX 3U
#define L3_BIN_SCORE_MAX_RX 4U

/* perLoop (NULL for none) gets each loop's residual power, l3sparse's power
 * map row. Returns 0, or -1 (outputs untouched) for a NULL frame or out, 0 or
 * too many TX/RX, loops of 0 or > L3_IQ16_MAX_LOOPS, or localBin >= binCount. */
int32_t l3_bin_score_iq16(const int16_t *frame, uint32_t binCount, uint32_t localBin,
                          uint32_t ntx, uint32_t nrx, uint32_t loops, l3_bin_obs_t *out,
                          float *perLoop);

#endif /* L3_BIN_SCORE_H */
