/* IWR6843 per-bin scoring. See l3_bin_score.h. */
#include "l3_bin_score.h"

#include <stddef.h>

#include "l3_iq16_stats.h"

int32_t l3_bin_score_iq16(const int16_t *frame, uint32_t binCount, uint32_t localBin,
                          uint32_t ntx, uint32_t nrx, uint32_t loops, l3_bin_obs_t *out,
                          float *perLoop)
{
    l3_iq16_bin_stats_t bin;
    l3_iq16_channel_stats_t channel;
    float loop0 = 0.0F;
    uint32_t loopStrideWords;
    uint32_t tx;

    if (frame == NULL || out == NULL || ntx == 0U || ntx > L3_BIN_SCORE_MAX_TX ||
        nrx == 0U || nrx > L3_BIN_SCORE_MAX_RX || loops == 0U ||
        loops > L3_IQ16_MAX_LOOPS || localBin >= binCount) {
        return -1;
    }
    loopStrideWords = ntx * nrx * binCount * 2U;
    l3_iq16_bin_stats_init(&bin, loops);
    for (tx = 0U; tx < ntx; tx++) {
        uint32_t rx;

        if (ntx == 3U && tx == 1U) {
            continue; /* the azimuth element */
        }
        for (rx = 0U; rx < nrx; rx++) {
            const int16_t *words = &frame[((tx * nrx + rx) * binCount + localBin) * 2U];

            if (l3_iq16_channel_stats(words, loops, loopStrideWords, &channel) == 0) {
                l3_iq16_bin_stats_add(&bin, &channel);
            }
        }
    }
    l3_iq16_bin_stats_finish(&bin, &out->energy, &out->peak, &loop0, &out->r1Re, &out->r1Im,
                             perLoop);
    out->loop0 = loop0;
    return 0;
}
