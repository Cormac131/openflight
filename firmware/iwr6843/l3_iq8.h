/* IWR6843 IQ8 quantisation, shared by the firmware and the host.
 *
 * The board stores compact IQ8 frames in three different ways, and a host
 * that wants to know what IQ8 costs a measurement must reproduce each one
 * bit for bit, not approximate it with a rounding cast:
 *
 *   CPU pack  (L3_RING_IQ8 without L3_IQ8_EDMA_PACK): the HWA right-shifts
 *             its range-FFT output by L3_IQ8_HWA_SHIFT, the rearm task finds
 *             the smallest extra shift that brings the frame's largest
 *             component under 128 (previewing every eighth complex sample
 *             with L3_IQ8_SPARSE_SCALE), rounds half away from zero, clips
 *             to int8 and records scale = 2^(hwaShift + packShift).
 *   EDMA pack (L3_IQ8_EDMA_PACK): the HWA right-shifts by the fixed
 *             iq8Scale shift and EDMA copies the LOW BYTE of each int16
 *             component. There is no clip: a component still outside int8
 *             after the shift wraps. scale = 2^shift.
 *   Dump time (L3_DUMP_IQ8): the ring is IQ16 and each frame is quantised
 *             as it streams, scale = ceil(maxAbs / 127), round half away
 *             from zero, clip.
 *
 * The HWA's output shift is hardware; this file models it as an arithmetic
 * shift with optional round-half-up and int16 saturation, and
 * scripts/hardware-test/iwr6843_iq8_hwa_probe.py is how the rounding flag
 * is settled against a board. Pure C, no hardware, compiled into l3_dump.c
 * and into the host library so both run the same arithmetic.
 */
#ifndef L3_IQ8_H
#define L3_IQ8_H

#include <stdint.h>

enum {
    L3_IQ8_PATH_CPU = 0,
    L3_IQ8_PATH_EDMA = 1,
    L3_IQ8_PATH_DUMP = 2
};

typedef struct {
    uint8_t  path;          /* L3_IQ8_PATH_* */
    uint8_t  hwaShift;      /* HWA output right shift before storage (CPU: 4, EDMA: iq8Scale) */
    uint8_t  hwaRounding;   /* 1: the HWA rounds half up before shifting; 0: truncates */
    uint8_t  sparseStride;  /* CPU path: preview every Nth complex sample (0 or 1: all) */
} l3_iq8_mode_t;

/* Fill mode with the firmware's production settings for one path. */
void l3_iq8_mode_defaults(l3_iq8_mode_t *mode, uint8_t path);
/* The HWA's output scaling as modelled here: arithmetic right shift of a
 * range-FFT component, saturated to int16. */
int16_t l3_iq8_hwa_scale(int16_t sample, uint8_t shift, uint8_t rounding);
/* The extra shift that brings the largest component of `components`
 * int16 values under 128, previewing every `stride`th complex sample (two
 * components) when stride > 1. This is l3_dump.c's pack shift. */
uint8_t l3_iq8_pack_shift(const int16_t *source, uint32_t components, uint32_t stride);
/* Round-half-away-from-zero shift with int8 clipping; counts a clip. */
int8_t l3_iq8_quantize_shift(int16_t sample, uint8_t shift, uint32_t *clipped);
/* Divide by a scale, round half away from zero, clip to int8 (dump path). */
int8_t l3_iq8_quantize_scale(int16_t sample, uint16_t scale);
/* ceil(maxAbs / 127) with the same integer arithmetic as the dump path. */
uint16_t l3_iq8_dump_scale(uint32_t maxAbs);
/* The EDMA byte copy: the low byte of a component, wrapping. */
int8_t l3_iq8_low_byte(int16_t sample);
/* Largest |component| over `components` values, at least 1. */
uint32_t l3_iq8_max_abs(const int16_t *source, uint32_t components);
/* Quantise one frame of `components` HWA range-FFT components (as the HWA
 * produced them before any output shift) the way the selected path stores
 * them. Writes int8 components and the frame scale; returns how many
 * components clipped (CPU, dump) or wrapped (EDMA). */
uint32_t l3_iq8_emulate_frame(const l3_iq8_mode_t *mode, const int16_t *source, int8_t *out,
                              uint32_t components, uint16_t *scaleOut);

#endif /* L3_IQ8_H */
