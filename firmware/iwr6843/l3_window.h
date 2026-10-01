/* IWR6843 range-FFT window: the coefficients the HWA's window RAM holds.
 *
 * The HWA multiplies each chirp's ADC samples by the window before the range
 * FFT. With HWA_FFT_WINDOW_SYMMETRIC it holds only the first half and
 * mirrors it, in Q17 (TI's demos: mathUtils_genWindow with
 * DPC_OBJDET_QFORMAT_RANGEFFT_WINDOW). The board ran unwindowed until
 * 2026-10-01: a strong return (the golfer's body) leaks ~13 dB down into its
 * neighbours, the level of the club's approach; Hann pushes that to ~31 dB.
 * Pure C, no hardware.
 */
#ifndef L3_WINDOW_H
#define L3_WINDOW_H

#include <stdint.h>

#define L3_WINDOW_Q            17
#define L3_WINDOW_MAX_SAMPLES  256U

enum {
    L3_RANGE_WINDOW_NONE = 0,  /* rectangular: the HWA's window off */
    L3_RANGE_WINDOW_HANN = 1,  /* symmetric Hann, w[n] = 0.5 - 0.5 cos(2 pi n / (N - 1)) */
    L3_RANGE_WINDOW_COUNT
};

/* The first n / 2 Q17 coefficients of a symmetric n-point Hann window into
 * out. n must be even, 4..L3_WINDOW_MAX_SAMPLES; otherwise nothing is written.
 * Returns how many were written. */
uint32_t l3_window_hann_q17(int32_t *out, uint32_t n);
/* "none" or "hann" -> *window; 0 on success, -1 (and *window untouched) otherwise. */
int32_t l3_window_parse(const char *text, uint8_t *window);
/* The name l3_window_parse reads, "?" for an unknown window. */
const char *l3_window_name(uint8_t window);

#endif /* L3_WINDOW_H */
