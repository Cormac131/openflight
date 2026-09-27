/* IWR6843 exact IQ16 channel statistics.
 *
 * The observation layer's per-bin numbers (burst-MTI residual energy,
 * per-loop residual power, lag-1 loop autocorrelation) come from int16 I/Q
 * samples. Summing their squares in float loses low bits at every add; this
 * module keeps the arithmetic in integers until one conversion at the end.
 * The loop mean is not an integer, so the residual is taken SCALED by the
 * loop count: r' = loops * x - sum(x) is exact in int32, its square in int64
 * (|r'| <= 2^21 for 16 loops), and the sums over loops and channels stay
 * far below 2^63. Dividing the totals by loops^2 once gives the same
 * quantities l3_verticalResidual computed in float, bit-exact up to that
 * final conversion. Pure C, no hardware.
 */
#ifndef L3_IQ16_STATS_H
#define L3_IQ16_STATS_H

#include <stdint.h>

#define L3_IQ16_MAX_LOOPS 16U

/* One (tx, rx) channel of one bin over the loops, scaled by loops (sums) and
 * loops^2 (products). */
typedef struct {
    uint32_t loops;
    int32_t  sumIm;                 /* over the loops */
    int32_t  sumRe;
    int64_t  energy;                /* sum over loops of |r'|^2 */
    int64_t  loopPower[L3_IQ16_MAX_LOOPS];
    int64_t  r1Re;                  /* sum over loops >= 1 of r'[l] conj(r'[l-1]) */
    int64_t  r1Im;
} l3_iq16_channel_stats_t;

/* Accumulate channels into one bin's statistics, still scaled. */
typedef struct {
    uint32_t loops;
    uint32_t channels;
    int64_t  energy;
    int64_t  loopPower[L3_IQ16_MAX_LOOPS];
    int64_t  r1Re;
    int64_t  r1Im;
} l3_iq16_bin_stats_t;

/* samples: the channel's first (Im, Re) pair; strideWords: int16 words from
 * one loop's pair to the next. Returns 0, or -1 for loops of 0 or > 16. */
int32_t l3_iq16_channel_stats(const int16_t *samples, uint32_t loops, uint32_t strideWords,
                              l3_iq16_channel_stats_t *out);
void l3_iq16_bin_stats_init(l3_iq16_bin_stats_t *bin, uint32_t loops);
void l3_iq16_bin_stats_add(l3_iq16_bin_stats_t *bin, const l3_iq16_channel_stats_t *channel);
/* The observation-layer quantities in physical units (divided by loops^2),
 * as floats: energy, the strongest loop, loop 0, r1. perLoop may be NULL. */
void l3_iq16_bin_stats_finish(const l3_iq16_bin_stats_t *bin, float *energy, float *peak,
                              float *loop0, float *r1Re, float *r1Im, float *perLoop);

#endif /* L3_IQ16_STATS_H */
