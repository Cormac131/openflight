/* See l3_iq16_stats.h. */
#include <string.h>

#include "l3_iq16_stats.h"

int32_t l3_iq16_channel_stats(const int16_t *samples, uint32_t loops, uint32_t strideWords,
                              l3_iq16_channel_stats_t *out)
{
    int32_t sumIm = 0;
    int32_t sumRe = 0;
    int32_t prevIm = 0;
    int32_t prevRe = 0;
    const int16_t *sample;
    uint32_t loop;

    memset(out, 0, sizeof(*out));
    if (loops == 0U || loops > L3_IQ16_MAX_LOOPS) {
        return -1;
    }
    out->loops = loops;
    sample = samples;
    for (loop = 0U; loop < loops; loop++) {
        sumIm += (int32_t)sample[0];
        sumRe += (int32_t)sample[1];
        sample += strideWords;
    }
    out->sumIm = sumIm;
    out->sumRe = sumRe;
    sample = samples;
    for (loop = 0U; loop < loops; loop++) {
        /* Residual scaled by loops: exact in int32 (|x| < 2^15, loops <= 16). */
        int32_t im = (int32_t)loops * (int32_t)sample[0] - sumIm;
        int32_t re = (int32_t)loops * (int32_t)sample[1] - sumRe;
        int64_t power = (int64_t)im * im + (int64_t)re * re;

        out->loopPower[loop] = power;
        out->energy += power;
        if (loop > 0U) {
            /* r'[loop] * conj(r'[loop - 1]) */
            out->r1Re += (int64_t)re * prevRe + (int64_t)im * prevIm;
            out->r1Im += (int64_t)im * prevRe - (int64_t)re * prevIm;
        }
        prevIm = im;
        prevRe = re;
        sample += strideWords;
    }
    return 0;
}

void l3_iq16_bin_stats_init(l3_iq16_bin_stats_t *bin, uint32_t loops)
{
    memset(bin, 0, sizeof(*bin));
    bin->loops = loops;
}

void l3_iq16_bin_stats_add(l3_iq16_bin_stats_t *bin, const l3_iq16_channel_stats_t *channel)
{
    uint32_t loop;

    if (channel->loops != bin->loops) {
        return;
    }
    bin->channels++;
    bin->energy += channel->energy;
    bin->r1Re += channel->r1Re;
    bin->r1Im += channel->r1Im;
    for (loop = 0U; loop < bin->loops; loop++) {
        bin->loopPower[loop] += channel->loopPower[loop];
    }
}

void l3_iq16_bin_stats_finish(const l3_iq16_bin_stats_t *bin, float *energy, float *peak,
                              float *loop0, float *r1Re, float *r1Im, float *perLoop)
{
    /* One division by loops^2 undoes the scaling; double keeps the 2^48
     * totals exact before the float conversion. */
    double scale = (bin->loops > 0U) ? 1.0 / ((double)bin->loops * (double)bin->loops) : 0.0;
    double best = 0.0;
    uint32_t loop;

    for (loop = 0U; loop < bin->loops; loop++) {
        double value = (double)bin->loopPower[loop] * scale;

        if (perLoop != NULL) {
            perLoop[loop] = (float)value;
        }
        if (value > best) {
            best = value;
        }
    }
    if (energy != NULL) {
        *energy = (float)((double)bin->energy * scale);
    }
    if (peak != NULL) {
        *peak = (float)best;
    }
    if (loop0 != NULL) {
        *loop0 = (bin->loops > 0U) ? (float)((double)bin->loopPower[0] * scale) : 0.0F;
    }
    if (r1Re != NULL) {
        *r1Re = (float)((double)bin->r1Re * scale);
    }
    if (r1Im != NULL) {
        *r1Im = (float)((double)bin->r1Im * scale);
    }
}
