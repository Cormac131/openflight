/* See l3_iq8.h. */
#include <string.h>

#include "l3_iq8.h"

#define L3_IQ8_CPU_HWA_SHIFT 4U
#define L3_IQ8_CPU_SPARSE_STRIDE 8U
#define L3_IQ8_EDMA_DEFAULT_SHIFT 7U   /* iq8Scale 128 */

void l3_iq8_mode_defaults(l3_iq8_mode_t *mode, uint8_t path)
{
    memset(mode, 0, sizeof(*mode));
    mode->path = path;
    mode->hwaRounding = 0U;
    if (path == L3_IQ8_PATH_CPU) {
        mode->hwaShift = L3_IQ8_CPU_HWA_SHIFT;
        mode->sparseStride = L3_IQ8_CPU_SPARSE_STRIDE;
    } else if (path == L3_IQ8_PATH_EDMA) {
        mode->hwaShift = L3_IQ8_EDMA_DEFAULT_SHIFT;
        mode->sparseStride = 1U;
    } else {
        mode->hwaShift = 0U;
        mode->sparseStride = 1U;
    }
}

int16_t l3_iq8_hwa_scale(int16_t sample, uint8_t shift, uint8_t rounding)
{
    int32_t value = (int32_t)sample;

    if (shift == 0U) {
        return sample;
    }
    if (rounding) {
        value += (int32_t)(1U << (shift - 1U));
    }
    /* Arithmetic shift: floor toward minus infinity, as a two's complement
     * shifter does. Written out because >> on a negative int is
     * implementation-defined in C99. */
    if (value < 0) {
        value = -(((-value) + (int32_t)(1U << shift) - 1) >> shift);
    } else {
        value >>= shift;
    }
    if (value > 32767) {
        value = 32767;
    } else if (value < -32768) {
        value = -32768;
    }
    return (int16_t)value;
}

uint32_t l3_iq8_max_abs(const int16_t *source, uint32_t components)
{
    uint32_t maxAbs = 1U;
    uint32_t component;

    for (component = 0U; component < components; component++) {
        int32_t value = (int32_t)source[component];
        uint32_t magnitude = (value < 0) ? (uint32_t)(-value) : (uint32_t)value;
        if (magnitude > maxAbs) {
            maxAbs = magnitude;
        }
    }
    return maxAbs;
}

uint8_t l3_iq8_pack_shift(const int16_t *source, uint32_t components, uint32_t stride)
{
    uint32_t maxAbs = 1U;
    uint32_t component;
    uint32_t step = (stride > 1U) ? 2U * stride : 1U;
    uint8_t shift = 0U;

    if (step == 1U) {
        maxAbs = l3_iq8_max_abs(source, components);
    } else {
        /* The sampled preview looks at both components of every strideth
         * complex sample, exactly as l3_iq8SampledPackShift did. */
        for (component = 0U; component + 1U < components; component += step) {
            int32_t iValue = (int32_t)source[component];
            int32_t qValue = (int32_t)source[component + 1U];
            uint32_t iMagnitude = (iValue < 0) ? (uint32_t)(-iValue) : (uint32_t)iValue;
            uint32_t qMagnitude = (qValue < 0) ? (uint32_t)(-qValue) : (uint32_t)qValue;

            if (iMagnitude > maxAbs) {
                maxAbs = iMagnitude;
            }
            if (qMagnitude > maxAbs) {
                maxAbs = qMagnitude;
            }
        }
    }
    while (maxAbs > 127U) {
        maxAbs = (maxAbs + 1U) >> 1U;
        shift++;
    }
    return shift;
}

int8_t l3_iq8_quantize_shift(int16_t sample, uint8_t shift, uint32_t *clipped)
{
    int32_t value = (int32_t)sample;
    int32_t quantized;

    if (shift == 0U) {
        quantized = value;
    } else {
        int32_t half = (int32_t)(1U << (shift - 1U));
        if (value >= 0) {
            quantized = (value + half) >> shift;
        } else {
            quantized = -((-value + half) >> shift);
        }
    }
    if (quantized > 127) {
        quantized = 127;
        if (clipped != NULL) {
            (*clipped)++;
        }
    } else if (quantized < -128) {
        quantized = -128;
        if (clipped != NULL) {
            (*clipped)++;
        }
    }
    return (int8_t)quantized;
}

int8_t l3_iq8_quantize_scale(int16_t sample, uint16_t scale)
{
    int32_t value = (int32_t)sample;
    int32_t half = (int32_t)scale / 2;
    int32_t quantized;

    if (scale == 0U) {
        scale = 1U;
    }
    if (value >= 0) {
        quantized = (value + half) / (int32_t)scale;
    } else {
        quantized = -((-value + half) / (int32_t)scale);
    }
    if (quantized > 127) {
        quantized = 127;
    } else if (quantized < -128) {
        quantized = -128;
    }
    return (int8_t)quantized;
}

uint16_t l3_iq8_dump_scale(uint32_t maxAbs)
{
    if (maxAbs < 1U) {
        maxAbs = 1U;
    }
    return (uint16_t)((maxAbs + 126U) / 127U);
}

int8_t l3_iq8_low_byte(int16_t sample)
{
    return (int8_t)((uint16_t)sample & 0xFFU);
}

uint32_t l3_iq8_emulate_frame(const l3_iq8_mode_t *mode, const int16_t *source, int8_t *out,
                              uint32_t components, uint16_t *scaleOut)
{
    uint32_t component;
    uint32_t clipped = 0U;

    if (mode->path == L3_IQ8_PATH_DUMP) {
        uint16_t scale = l3_iq8_dump_scale(l3_iq8_max_abs(source, components));

        for (component = 0U; component < components; component++) {
            int32_t value = (int32_t)source[component];
            uint32_t magnitude = (value < 0) ? (uint32_t)(-value) : (uint32_t)value;

            out[component] = l3_iq8_quantize_scale(source[component], scale);
            if (magnitude > 127U * (uint32_t)scale + (uint32_t)scale / 2U) {
                clipped++;
            }
        }
        *scaleOut = scale;
        return clipped;
    }
    if (mode->path == L3_IQ8_PATH_EDMA) {
        for (component = 0U; component < components; component++) {
            int16_t shifted = l3_iq8_hwa_scale(source[component], mode->hwaShift, mode->hwaRounding);

            out[component] = l3_iq8_low_byte(shifted);
            if (shifted > 127 || shifted < -128) {
                clipped++;
            }
        }
        *scaleOut = (uint16_t)(1U << mode->hwaShift);
        return clipped;
    }
    {
        /* CPU pack: the HWA shift lands in the scratch frame, then the pack
         * shift is chosen from (a preview of) that frame. The shifted frame
         * is not kept, so the shift is applied twice: once for the preview
         * and once per component. */
        uint8_t packShift;
        uint32_t stride = (mode->sparseStride > 1U) ? mode->sparseStride : 1U;

        for (component = 0U; component < components; component++) {
            out[component] = (int8_t)0;
        }
        /* Preview pass over the HWA-shifted values. */
        {
            uint32_t maxAbs = 1U;
            uint32_t step = (stride > 1U) ? 2U * stride : 1U;

            for (component = 0U; component + (step > 1U ? 1U : 0U) < components;
                 component += step) {
                int32_t iValue = (int32_t)l3_iq8_hwa_scale(source[component], mode->hwaShift,
                                                           mode->hwaRounding);
                uint32_t iMagnitude = (iValue < 0) ? (uint32_t)(-iValue) : (uint32_t)iValue;

                if (iMagnitude > maxAbs) {
                    maxAbs = iMagnitude;
                }
                if (step > 1U) {
                    int32_t qValue = (int32_t)l3_iq8_hwa_scale(source[component + 1U],
                                                               mode->hwaShift, mode->hwaRounding);
                    uint32_t qMagnitude = (qValue < 0) ? (uint32_t)(-qValue) : (uint32_t)qValue;

                    if (qMagnitude > maxAbs) {
                        maxAbs = qMagnitude;
                    }
                }
            }
            packShift = 0U;
            while (maxAbs > 127U) {
                maxAbs = (maxAbs + 1U) >> 1U;
                packShift++;
            }
        }
        for (component = 0U; component < components; component++) {
            int16_t shifted = l3_iq8_hwa_scale(source[component], mode->hwaShift, mode->hwaRounding);

            out[component] = l3_iq8_quantize_shift(shifted, packShift, &clipped);
        }
        *scaleOut = (uint16_t)(1U << (mode->hwaShift + packShift));
        return clipped;
    }
}
