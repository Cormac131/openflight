/* See l3_adaptive.h. */
#include <stdio.h>
#include <string.h>

#include "l3_adaptive.h"

void l3_adaptive_cfg_defaults(l3_adaptive_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->enabled = 0U;
    cfg->approachBins = 24U;  /* about 1.1 m of downswing at 46.9 mm bins */
    cfg->marginBins = 4U;     /* the ball and 19 cm short of it */
}

static uint8_t l3_adaptive_clip(int32_t start, uint32_t width, uint32_t fftBins)
{
    int32_t last = (int32_t)fftBins - (int32_t)width;

    if (last < 0) {
        last = 0;
    }
    if (start < 0) {
        start = 0;
    }
    if (start > last) {
        start = last;
    }
    return (uint8_t)start;
}

int32_t l3_adaptive_windows(const l3_adaptive_cfg_t *cfg, uint32_t ballBin, uint32_t fftBins,
                            uint32_t preBins, uint32_t impactBins, uint32_t postBins,
                            l3_adaptive_windows_t *out)
{
    int32_t ball = (int32_t)ballBin;

    memset(out, 0, sizeof(*out));
    if (!cfg->enabled || fftBins == 0U || ballBin >= fftBins || preBins == 0U ||
        postBins == 0U) {
        return 0;
    }
    out->preStart = l3_adaptive_clip(ball - (int32_t)cfg->approachBins, preBins, fftBins);
    out->impactStart = l3_adaptive_clip(ball - (int32_t)cfg->marginBins,
                                        (impactBins > 0U) ? impactBins : postBins, fftBins);
    out->postStart = l3_adaptive_clip(ball - (int32_t)cfg->marginBins, postBins, fftBins);
    out->lateStart = l3_adaptive_clip((int32_t)out->postStart + (int32_t)(postBins / 2U),
                                      postBins, fftBins);
    return 1;
}

int32_t l3_adaptive_differs(const l3_adaptive_windows_t *out, uint32_t preStart,
                            uint32_t impactStart, uint32_t postStart, uint32_t lateStart)
{
    return (out->preStart != preStart || out->impactStart != impactStart ||
            out->postStart != postStart || out->lateStart != lateStart)
               ? 1
               : 0;
}

int32_t l3_adaptive_format(const l3_adaptive_cfg_t *cfg, const l3_adaptive_windows_t *out,
                           char *text, uint32_t cap)
{
    return snprintf(text, cap, "adaptive enabled=%u approach=%u margin=%u pre=%u impact=%u post=%u "
                               "late=%u",
                    (unsigned)cfg->enabled, (unsigned)cfg->approachBins,
                    (unsigned)cfg->marginBins, (unsigned)out->preStart,
                    (unsigned)out->impactStart, (unsigned)out->postStart,
                    (unsigned)out->lateStart);
}
