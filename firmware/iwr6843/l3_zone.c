/* See l3_zone.h. */
#include <math.h>
#include <string.h>

#include "l3_zone.h"

static const char *const kReasonNames[L3_ZONE_REASONS] = {
    "no_angles", "short", "past", "left", "right", "low", "high"
};

void l3_zone_cfg_defaults(l3_zone_cfg_t *cfg, float teeForwardM, float radarHeightM)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->teeForwardM = teeForwardM;
    cfg->lateralM = 0.0F;
    cfg->shortM = 1.2F;
    cfg->pastM = 0.3F;
    cfg->halfWidthM = 0.3F;
    cfg->radarHeightM = radarHeightM;
    cfg->minHeightM = -0.1F;
    cfg->maxHeightM = 1.2F;
    cfg->requireAngles = 1U;
}

int32_t l3_zone_cfg_check(const l3_zone_cfg_t *cfg)
{
    const float values[] = { cfg->teeForwardM, cfg->lateralM, cfg->shortM, cfg->pastM,
                             cfg->halfWidthM, cfg->radarHeightM, cfg->minHeightM,
                             cfg->maxHeightM };
    uint32_t i;

    for (i = 0U; i < sizeof(values) / sizeof(values[0]); i++) {
        if (!isfinite(values[i])) {
            return -1;
        }
    }
    if (cfg->shortM < 0.0F || cfg->pastM < 0.0F || cfg->halfWidthM < 0.0F ||
        cfg->minHeightM > cfg->maxHeightM) {
        return -1;
    }
    return 0;
}

uint32_t l3_zone_check(const l3_zone_cfg_t *cfg, const l3_vec3_t *golf, uint8_t anglesValid)
{
    uint32_t reasons = L3_ZONE_INSIDE;
    uint8_t located = ((anglesValid & L3_ZONE_ANGLES_BOTH) == L3_ZONE_ANGLES_BOTH) ? 1U : 0U;

    if (golf->x < cfg->teeForwardM - cfg->shortM) {
        reasons |= L3_ZONE_SHORT;
    } else if (golf->x > cfg->teeForwardM + cfg->pastM) {
        reasons |= L3_ZONE_PAST;
    }
    if (!located) {
        /* Boresight-filled: the lateral offset and height are not measured. */
        return reasons | (cfg->requireAngles ? L3_ZONE_NO_ANGLES : 0U);
    }
    if (golf->y < cfg->lateralM - cfg->halfWidthM) {
        reasons |= L3_ZONE_LEFT;
    } else if (golf->y > cfg->lateralM + cfg->halfWidthM) {
        reasons |= L3_ZONE_RIGHT;
    }
    if (golf->z + cfg->radarHeightM < cfg->minHeightM) {
        reasons |= L3_ZONE_LOW;
    } else if (golf->z + cfg->radarHeightM > cfg->maxHeightM) {
        reasons |= L3_ZONE_HIGH;
    }
    return reasons;
}

const char *l3_zone_reason_name(uint32_t bit)
{
    uint32_t i;

    for (i = 0U; i < L3_ZONE_REASONS; i++) {
        if (bit == (1U << i)) {
            return kReasonNames[i];
        }
    }
    return "?";
}
