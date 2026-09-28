/* See l3_impact.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_impact.h"
#include "l3_text.h"

static const char *const kWhyNames[L3_IMPACT_WHY_COUNT] = {
    "none", "noball", "nodelivery", "slow", "unsure", "far", "pending", "passed", "fired"
};

void l3_impact_cfg_defaults(l3_impact_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->toleranceM = 0.15F;     /* a clubhead, a ball and a bin of range error */
    cfg->horizonS = 0.004F;      /* one 3 ms frame plus scheduling slack */
    cfg->minSpeedMps = 5.0F;     /* slower than any swing that reaches the ball */
    cfg->minConfidence = 0.2F;
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
    impact->closestM = 0.0F;
    impact->offsetS = 0.0F;
    impact->impactTimestampUs = 0U;
    memset(&impact->contact, 0, sizeof(impact->contact));
    memset(&impact->velocity, 0, sizeof(impact->velocity));
}

void l3_impact_closest(const l3_vec3_t *position, const l3_vec3_t *velocity,
                       const l3_vec3_t *ball, float *distanceM, float *offsetS,
                       l3_vec3_t *contact)
{
    float dx = ball->x - position->x;
    float dy = ball->y - position->y;
    float dz = ball->z - position->z;
    float speedSq = velocity->x * velocity->x + velocity->y * velocity->y +
                    velocity->z * velocity->z;
    float t = 0.0F;

    if (speedSq > 0.0F) {
        t = (dx * velocity->x + dy * velocity->y + dz * velocity->z) / speedSq;
    }
    contact->x = position->x + velocity->x * t;
    contact->y = position->y + velocity->y * t;
    contact->z = position->z + velocity->z * t;
    dx = ball->x - contact->x;
    dy = ball->y - contact->y;
    dz = ball->z - contact->z;
    *distanceM = sqrtf(dx * dx + dy * dy + dz * dz);
    *offsetS = t;
}

static int32_t l3_impact_note(l3_impact_t *impact, uint8_t why)
{
    impact->why = why;
    impact->counters[why]++;
    return (why == L3_IMPACT_WHY_FIRED) ? 1 : 0;
}

int32_t l3_impact_update(l3_impact_t *impact, const l3_delivery_t *delivery,
                         const l3_vec3_t *ball, uint8_t ballValid)
{
    const l3_impact_cfg_t *cfg = &impact->cfg;
    float distance;
    float offset;
    l3_vec3_t contact;

    if (impact->fired) {
        return 0;
    }
    if (!ballValid) {
        return l3_impact_note(impact, L3_IMPACT_WHY_NO_BALL);
    }
    if (delivery == NULL || !delivery->speedValid || delivery->points < 3U) {
        return l3_impact_note(impact, L3_IMPACT_WHY_NO_DELIVERY);
    }
    if (delivery->speedMps < cfg->minSpeedMps) {
        return l3_impact_note(impact, L3_IMPACT_WHY_SLOW);
    }
    if (delivery->confidence < cfg->minConfidence) {
        return l3_impact_note(impact, L3_IMPACT_WHY_UNSURE);
    }
    l3_impact_closest(&delivery->position, &delivery->velocity, ball, &distance, &offset,
                      &contact);
    impact->closestM = distance;
    impact->offsetS = offset;
    impact->contact = contact;
    if (distance > cfg->toleranceM) {
        return l3_impact_note(impact, L3_IMPACT_WHY_FAR);
    }
    if (offset > cfg->horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PENDING);
    }
    if (offset < -cfg->horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PASSED);
    }
    impact->fired = 1U;
    impact->velocity = delivery->velocity;
    {
        float stamp = (float)delivery->timestampUs + offset * 1.0e6F;

        impact->impactTimestampUs = (stamp > 0.0F) ? (uint32_t)(stamp + 0.5F) : 0U;
    }
    return l3_impact_note(impact, L3_IMPACT_WHY_FIRED);
}

int32_t l3_impact_update_range(l3_impact_t *impact, const l3_fit_estimate_t *clubIn,
                               uint32_t nowUs)
{
    float offset;

    if (impact->fired) {
        return 0;
    }
    if (clubIn == NULL || clubIn->why != L3_FIT_WHY_OK) {
        return l3_impact_note(impact, L3_IMPACT_WHY_NO_DELIVERY);
    }
    offset = (clubIn->timeUs - (float)nowUs) * 1.0e-6F;
    impact->offsetS = offset;
    impact->closestM = 0.0F;
    if (offset > impact->cfg.horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PENDING);
    }
    if (offset < -impact->cfg.horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PASSED);
    }
    impact->fired = 1U;
    impact->impactTimestampUs = (clubIn->timeUs > 0.0F) ? (uint32_t)(clubIn->timeUs + 0.5F) : 0U;
    return l3_impact_note(impact, L3_IMPACT_WHY_FIRED);
}

const char *l3_impact_why_name(uint8_t why)
{
    return (why < L3_IMPACT_WHY_COUNT) ? kWhyNames[why] : "?";
}

int32_t l3_impact_format(const l3_impact_t *impact, char *out, uint32_t cap)
{
    char closestText[16];
    char offsetText[16];
    char xText[16];
    char yText[16];
    char zText[16];

    l3_text_fixed2(impact->closestM * 100.0F, closestText, sizeof(closestText));
    l3_text_fixed2(impact->offsetS * 1000.0F, offsetText, sizeof(offsetText));
    l3_text_fixed2(impact->contact.x, xText, sizeof(xText));
    l3_text_fixed2(impact->contact.y, yText, sizeof(yText));
    l3_text_fixed2(impact->contact.z, zText, sizeof(zText));
    return snprintf(out, cap,
                    "impact fired=%u why=%s closestcm=%s offsetms=%s t=%u contact=%s,%s,%s "
                    "far=%u pending=%u passed=%u fired_n=%u",
                    (unsigned)impact->fired, l3_impact_why_name(impact->why), closestText,
                    offsetText, (unsigned)impact->impactTimestampUs, xText, yText, zText,
                    (unsigned)impact->counters[L3_IMPACT_WHY_FAR],
                    (unsigned)impact->counters[L3_IMPACT_WHY_PENDING],
                    (unsigned)impact->counters[L3_IMPACT_WHY_PASSED],
                    (unsigned)impact->counters[L3_IMPACT_WHY_FIRED]);
}
