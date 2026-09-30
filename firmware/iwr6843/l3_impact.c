/* See l3_impact.h. */
#include <stdio.h>
#include <string.h>

#include "l3_impact.h"
#include "l3_text.h"

static const char *const kWhyNames[L3_IMPACT_WHY_COUNT] = {
    "none", "nodelivery", "pending", "passed", "fired"
};

void l3_impact_cfg_defaults(l3_impact_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->horizonS = 0.004F;      /* one 3 ms frame plus scheduling slack */
}

void l3_impact_init(l3_impact_t *impact, const l3_impact_cfg_t *cfg)
{
    memset(impact, 0, sizeof(*impact));
    impact->cfg = *cfg;
}

void l3_impact_rearm(l3_impact_t *impact)
{
    impact->fired = 0U;
    impact->why = L3_IMPACT_WHY_NONE;
    impact->offsetS = 0.0F;
    impact->impactTimestampUs = 0U;
}

static int32_t l3_impact_note(l3_impact_t *impact, uint8_t why)
{
    impact->why = why;
    impact->counters[why]++;
    return (why == L3_IMPACT_WHY_FIRED) ? 1 : 0;
}

int32_t l3_impact_update_range(l3_impact_t *impact, const l3_fit_estimate_t *clubIn,
                               uint32_t nowUs)
{
    uint32_t stamp;
    float offset;

    if (impact->fired) {
        return 0;
    }
    if (clubIn == NULL || clubIn->why != L3_FIT_WHY_OK) {
        return l3_impact_note(impact, L3_IMPACT_WHY_NO_DELIVERY);
    }
    /* Rounded to a timestamp first so the difference is wrap-safe. */
    stamp = l3_round_us(clubIn->timeUs);
    offset = (float)(int32_t)(stamp - nowUs) * 1.0e-6F;
    impact->offsetS = offset;
    if (offset > impact->cfg.horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PENDING);
    }
    if (offset < -impact->cfg.horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PASSED);
    }
    impact->fired = 1U;
    impact->impactTimestampUs = stamp;
    return l3_impact_note(impact, L3_IMPACT_WHY_FIRED);
}

const char *l3_impact_why_name(uint8_t why)
{
    return (why < L3_IMPACT_WHY_COUNT) ? kWhyNames[why] : "?";
}

int32_t l3_impact_format(const l3_impact_t *impact, char *out, uint32_t cap)
{
    char offsetText[16];

    l3_text_fixed2(impact->offsetS * 1000.0F, offsetText, sizeof(offsetText));
    return snprintf(out, cap,
                    "impact fired=%u why=%s offsetms=%s t=%u pending=%u passed=%u fired_n=%u",
                    (unsigned)impact->fired, l3_impact_why_name(impact->why), offsetText,
                    (unsigned)impact->impactTimestampUs,
                    (unsigned)impact->counters[L3_IMPACT_WHY_PENDING],
                    (unsigned)impact->counters[L3_IMPACT_WHY_PASSED],
                    (unsigned)impact->counters[L3_IMPACT_WHY_FIRED]);
}
