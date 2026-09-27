/* See l3_text.h. */
#include <stdio.h>

#include "l3_text.h"

#define L3_TEXT_RAD_TO_DEG 57.29577951F

void l3_text_fixed(float value, uint32_t decimals, char *out, uint32_t cap)
{
    const char *sign = (value < 0.0F) ? "-" : "";
    uint32_t scale = (decimals >= 2U) ? 100U : (decimals == 1U) ? 10U : 1U;
    unsigned whole;
    unsigned fraction;

    if (value < 0.0F) {
        value = -value;
    }
    if (value > 4.0e9F) {
        value = 4.0e9F;
    }
    whole = (unsigned)value;
    fraction = (unsigned)((value - (float)whole) * (float)scale + 0.5F);
    if (fraction >= scale) {
        whole++;
        fraction = 0U;
    }
    if (decimals >= 2U) {
        (void)snprintf(out, cap, "%s%u.%02u", sign, whole, fraction);
    } else if (decimals == 1U) {
        (void)snprintf(out, cap, "%s%u.%01u", sign, whole, fraction);
    } else {
        (void)snprintf(out, cap, "%s%u", sign, whole);
    }
}

void l3_text_fixed2(float value, char *out, uint32_t cap)
{
    l3_text_fixed(value, 2U, out, cap);
}

void l3_text_degrees2(float radians, char *out, uint32_t cap)
{
    l3_text_fixed2(radians * L3_TEXT_RAD_TO_DEG, out, cap);
}
