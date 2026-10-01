/* See l3_window.h. */
#include <math.h>
#include <string.h>

#include "l3_window.h"

static const char *const kWindowNames[L3_RANGE_WINDOW_COUNT] = {"none", "hann"};

uint32_t l3_window_hann_q17(int32_t *out, uint32_t n)
{
    const double pi = 3.14159265358979323846;
    uint32_t i;

    if (out == NULL || n < 4U || n > L3_WINDOW_MAX_SAMPLES || (n & 1U) != 0U) {
        return 0U;
    }
    for (i = 0U; i < n / 2U; i++) {
        double w = 0.5 - 0.5 * cos(2.0 * pi * (double)i / (double)(n - 1U));

        out[i] = (int32_t)floor(w * (double)(1L << L3_WINDOW_Q) + 0.5);
    }
    return n / 2U;
}

int32_t l3_window_parse(const char *text, uint8_t *window)
{
    uint8_t i;

    if (text == NULL || window == NULL) {
        return -1;
    }
    for (i = 0U; i < (uint8_t)L3_RANGE_WINDOW_COUNT; i++) {
        if (strcmp(text, kWindowNames[i]) == 0) {
            *window = i;
            return 0;
        }
    }
    return -1;
}

const char *l3_window_name(uint8_t window)
{
    return (window < (uint8_t)L3_RANGE_WINDOW_COUNT) ? kWindowNames[window] : "?";
}
