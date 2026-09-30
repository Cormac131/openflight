/* See l3_result.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_result.h"
#include "l3_text.h"

static const char *const kMetricNames[L3_RESULT_METRICS] = {
    "ball_speed", "vertical_launch", "horizontal_launch", "club_speed", "club_path",
    "angle_of_attack", "spin_rate", "spin_axis", "impact_range"
};

static const char *const kVerdictNames[3] = {"invalid", "partial", "valid"};

static void l3_result_set(l3_shot_result_t *out, uint32_t index, float value, float confidence,
                          uint32_t flags)
{
    out->metric[index].value = value;
    out->metric[index].confidence = confidence;
    out->metric[index].flags = flags | L3_MEAS_VALID;
    out->validFlags |= (1U << index);
}

static int32_t l3_result_within(float value, float low, float high)
{
    return (value >= low && value <= high) ? 1 : 0;
}

static void l3_result_flagBounds(l3_shot_result_t *out, uint32_t index, float low, float high)
{
    l3_measurement_t *m = &out->metric[index];

    if ((m->flags & L3_MEAS_VALID) && !l3_result_within(m->value, low, high)) {
        m->flags |= L3_MEAS_IMPLAUSIBLE;
    }
}

static int32_t l3_result_ok(const l3_shot_result_t *out, uint32_t index)
{
    uint32_t flags = out->metric[index].flags;

    return ((flags & L3_MEAS_VALID) && !(flags & L3_MEAS_IMPLAUSIBLE)) ? 1 : 0;
}

void l3_result_build(const l3_shot_t *shot, const l3_ball_track_t *ball, const l3_launch_t *launch,
                     const l3_impact_fit_t *fit, uint32_t shotId, uint8_t ballLocked,
                     l3_shot_result_t *out)
{
    const l3_delivery_t *delivery = &shot->delivery;
    uint32_t fallback = ballLocked ? 0U : L3_MEAS_FALLBACK;
    uint32_t quality = 0U;
    float originRange;

    memset(out, 0, sizeof(*out));
    if (fit != NULL) {
        out->impactFit = *fit;
    } else {
        l3_impact_fit_reset(&out->impactFit);
    }
    out->version = L3_RESULT_VERSION;
    out->shotId = shotId;
    out->impactTimestampUs = shot->impactTimestampUs;
    out->impactSource = shot->impactSource;
    out->clubPoints = (uint8_t)((shot->clubPoints > 255U) ? 255U : shot->clubPoints);
    out->ballPoints = (uint8_t)((ball->core.count > 255U) ? 255U : ball->core.count);

    /* Ball flight. */
    if (launch != NULL && launch->speedValid && launch->points >= L3_RESULT_MIN_BALL_POINTS) {
        uint32_t radial = (launch->hlaValid || launch->vlaValid) ? 0U : L3_MEAS_RADIAL_ONLY;

        l3_result_set(out, L3_METRIC_BALL_SPEED, launch->speedMps, launch->confidence,
                      L3_MEAS_MEASURED | radial | fallback);
        if (launch->vlaValid) {
            l3_result_set(out, L3_METRIC_VERTICAL_LAUNCH, launch->vlaRad, launch->confidence,
                          L3_MEAS_MEASURED | fallback);
        }
        if (launch->hlaValid) {
            l3_result_set(out, L3_METRIC_HORIZONTAL_LAUNCH, launch->hlaRad, launch->confidence,
                          L3_MEAS_MEASURED | fallback);
        }
    }
    /* Club delivery, as frozen at impact. */
    if (shot->state >= L3_SHOT_IMPACT && delivery->speedValid &&
        delivery->points >= L3_RESULT_MIN_CLUB_POINTS) {
        uint32_t radial = (delivery->pathValid || delivery->attackValid) ? 0U : L3_MEAS_RADIAL_ONLY;

        l3_result_set(out, L3_METRIC_CLUB_SPEED, delivery->speedMps, delivery->confidence,
                      L3_MEAS_MEASURED | radial);
        if (delivery->pathValid) {
            l3_result_set(out, L3_METRIC_CLUB_PATH, delivery->pathRad, delivery->confidence,
                          L3_MEAS_MEASURED);
        }
        if (delivery->attackValid) {
            l3_result_set(out, L3_METRIC_ANGLE_OF_ATTACK, delivery->attackRad,
                          delivery->confidence, L3_MEAS_MEASURED);
        }
    }
    if (shot->state >= L3_SHOT_IMPACT) {
        originRange = l3_frames_speed(&shot->ballOrigin);
        l3_result_set(out, L3_METRIC_IMPACT_RANGE, originRange, ballLocked ? 1.0F : 0.5F,
                      (ballLocked ? L3_MEAS_MEASURED : 0U) | fallback);
    }
    /* Spin stays invalid until there is a measurement to report. */

    /* Plausibility. */
    l3_result_flagBounds(out, L3_METRIC_BALL_SPEED, L3_RESULT_BALL_SPEED_MIN_MPS,
                         L3_RESULT_BALL_SPEED_MAX_MPS);
    l3_result_flagBounds(out, L3_METRIC_CLUB_SPEED, L3_RESULT_CLUB_SPEED_MIN_MPS,
                         L3_RESULT_CLUB_SPEED_MAX_MPS);
    l3_result_flagBounds(out, L3_METRIC_VERTICAL_LAUNCH, L3_RESULT_VLA_MIN_RAD,
                         L3_RESULT_VLA_MAX_RAD);
    l3_result_flagBounds(out, L3_METRIC_HORIZONTAL_LAUNCH, -L3_RESULT_HLA_MAX_RAD,
                         L3_RESULT_HLA_MAX_RAD);
    l3_result_flagBounds(out, L3_METRIC_CLUB_PATH, -L3_RESULT_PATH_MAX_RAD,
                         L3_RESULT_PATH_MAX_RAD);
    l3_result_flagBounds(out, L3_METRIC_ANGLE_OF_ATTACK, -L3_RESULT_AOA_MAX_RAD,
                         L3_RESULT_AOA_MAX_RAD);
    if (l3_result_ok(out, L3_METRIC_BALL_SPEED) && l3_result_ok(out, L3_METRIC_CLUB_SPEED) &&
        out->metric[L3_METRIC_CLUB_SPEED].value > 0.0F) {
        out->smash = out->metric[L3_METRIC_BALL_SPEED].value /
                     out->metric[L3_METRIC_CLUB_SPEED].value;
        if (l3_result_within(out->smash, L3_RESULT_SMASH_MIN, L3_RESULT_SMASH_MAX)) {
            quality |= L3_QUALITY_SMASH_PLAUSIBLE;
        } else {
            /* Bad tracking shows up as impossible smash: doubt both. */
            out->metric[L3_METRIC_BALL_SPEED].flags |= L3_MEAS_IMPLAUSIBLE;
            out->metric[L3_METRIC_CLUB_SPEED].flags |= L3_MEAS_IMPLAUSIBLE;
        }
    }

    /* Evidence. */
    if (ballLocked) {
        quality |= L3_QUALITY_BALL_LOCKED;
    }
    if (shot->state >= L3_SHOT_IMPACT) {
        quality |= L3_QUALITY_IMPACT_IDENTIFIED;
    }
    if (fit != NULL && fit->verdict == L3_FIT_VERDICT_INCONSISTENT) {
        quality |= L3_QUALITY_IMPACT_UNCERTAIN;
    }
    /* Any launch speed, range-only included: that is what the evidence was
     * measured on, and a 3D speed only reads higher. */
    if (launch != NULL && (out->validFlags & (1U << L3_METRIC_BALL_SPEED)) &&
        delivery->speedValid &&
        delivery->radialSpeedMps > 0.0F &&
        launch->speedMps < L3_RESULT_BALL_OVER_APPROACH_MIN * delivery->radialSpeedMps) {
        quality |= L3_QUALITY_BALL_SLOWER_THAN_CLUB;
    }
    if (shot->clubPoints >= L3_RESULT_MIN_CLUB_POINTS && delivery->speedValid) {
        quality |= L3_QUALITY_CLUB_TRACK;
        if (delivery->points == shot->clubPoints ||
            delivery->points >= L3_TRACK_FULL_POINTS) {
            quality |= L3_QUALITY_CLUB_CONTINUOUS;
        }
    }
    if (ball->confirmed) {
        quality |= L3_QUALITY_BALL_FROM_ORIGIN;
        if (ball->counters[L3_BALL_TRACK_WHY_COASTED] == 0U) {
            quality |= L3_QUALITY_BALL_CONTINUOUS;
        }
    }
    if ((!(out->validFlags & (1U << L3_METRIC_BALL_SPEED)) ||
         l3_result_ok(out, L3_METRIC_BALL_SPEED)) &&
        (!(out->validFlags & (1U << L3_METRIC_CLUB_SPEED)) ||
         l3_result_ok(out, L3_METRIC_CLUB_SPEED)) &&
        (out->validFlags & ((1U << L3_METRIC_BALL_SPEED) | (1U << L3_METRIC_CLUB_SPEED)))) {
        quality |= L3_QUALITY_SPEEDS_PLAUSIBLE;
    }
    if ((launch == NULL || !launch->speedValid || launch->residualM <= L3_RESULT_RESIDUAL_MAX_M) &&
        (!delivery->speedValid || delivery->residualM <= L3_RESULT_RESIDUAL_MAX_M)) {
        quality |= L3_QUALITY_RESIDUALS_OK;
    }
    if (l3_result_ok(out, L3_METRIC_VERTICAL_LAUNCH) &&
        l3_result_ok(out, L3_METRIC_HORIZONTAL_LAUNCH) &&
        (!(out->validFlags & (1U << L3_METRIC_CLUB_PATH)) || l3_result_ok(out, L3_METRIC_CLUB_PATH)) &&
        (!(out->validFlags & (1U << L3_METRIC_ANGLE_OF_ATTACK)) ||
         l3_result_ok(out, L3_METRIC_ANGLE_OF_ATTACK))) {
        quality |= L3_QUALITY_ANGLES_PLAUSIBLE;
    }
    out->qualityFlags = quality;

    /* Verdict. */
    {
        int32_t flight = l3_result_ok(out, L3_METRIC_BALL_SPEED) &&
                         l3_result_ok(out, L3_METRIC_VERTICAL_LAUNCH) &&
                         l3_result_ok(out, L3_METRIC_HORIZONTAL_LAUNCH) &&
                         (quality & L3_QUALITY_BALL_FROM_ORIGIN) &&
                         !(quality & L3_QUALITY_BALL_SLOWER_THAN_CLUB);
        int32_t club = l3_result_ok(out, L3_METRIC_CLUB_SPEED) &&
                       (quality & L3_QUALITY_CLUB_TRACK);

        if (flight && club && (quality & L3_QUALITY_IMPACT_IDENTIFIED) &&
            (quality & L3_QUALITY_RESIDUALS_OK) && (quality & L3_QUALITY_SPEEDS_PLAUSIBLE)) {
            out->verdict = L3_RESULT_VALID;
        } else if (l3_result_ok(out, L3_METRIC_BALL_SPEED) || club) {
            out->verdict = L3_RESULT_PARTIAL;
        } else {
            out->verdict = L3_RESULT_INVALID;
        }
    }
}

static uint8_t *l3_result_putU32(uint8_t *out, uint32_t value)
{
    out[0] = (uint8_t)(value & 0xFFU);
    out[1] = (uint8_t)((value >> 8) & 0xFFU);
    out[2] = (uint8_t)((value >> 16) & 0xFFU);
    out[3] = (uint8_t)((value >> 24) & 0xFFU);
    return out + 4;
}

static uint8_t *l3_result_putF32(uint8_t *out, float value)
{
    uint32_t bits;

    memcpy(&bits, &value, sizeof(bits));
    return l3_result_putU32(out, bits);
}

uint32_t l3_result_serialize(const l3_shot_result_t *result, uint8_t *out, uint32_t cap)
{
    uint8_t *p = out;
    uint32_t i;

    if (cap < L3_RESULT_PACKET_BYTES) {
        return 0U;
    }
    p = l3_result_putU32(p, result->version);
    p = l3_result_putU32(p, result->shotId);
    for (i = 0U; i < L3_RESULT_METRICS; i++) {
        p = l3_result_putF32(p, result->metric[i].value);
    }
    p = l3_result_putU32(p, result->validFlags);
    p = l3_result_putU32(p, result->qualityFlags);
    for (i = 0U; i < L3_RESULT_METRICS; i++) {
        p = l3_result_putF32(p, result->metric[i].confidence);
    }
    p = l3_result_putU32(p, result->impactTimestampUs);
    *p++ = result->verdict;
    *p++ = result->impactSource;
    *p++ = result->clubPoints;
    *p++ = result->ballPoints;
    p = l3_result_putF32(p, result->smash);
    *p++ = result->impactFit.verdict;
    *p++ = result->impactFit.droppedTrack;
    *p++ = result->impactFit.noLock;
    *p++ = 0U;
    p = l3_result_putF32(p, result->impactFit.impactUs);
    p = l3_result_putF32(p, result->impactFit.spreadUs);
    p = l3_result_putF32(p, result->impactFit.refinedMinusTriggerUs);
    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &result->impactFit.track[i];

        *p++ = e->why;
        *p++ = (uint8_t)((e->points > 255U) ? 255U : e->points);
        *p++ = 0U;
        *p++ = 0U;
        p = l3_result_putF32(p, e->timeUs);
        p = l3_result_putF32(p, e->sigmaUs);
        p = l3_result_putF32(p, e->speedMps);
    }
    return (uint32_t)(p - out);
}

const char *l3_result_metric_name(uint32_t index)
{
    return (index < L3_RESULT_METRICS) ? kMetricNames[index] : "?";
}

const char *l3_result_verdict_name(uint8_t verdict)
{
    return (verdict < 3U) ? kVerdictNames[verdict] : "?";
}

int32_t l3_result_format(const l3_shot_result_t *result, char *out, uint32_t cap)
{
    char smashText[16];
    char sourceText[24];

    l3_text_fixed2(result->smash, smashText, sizeof(smashText));
    return snprintf(out, cap,
                    "result v%u shot=%u verdict=%s valid=0x%x quality=0x%x impact=%u source=%s "
                    "club=%u ball=%u smash=%s",
                    (unsigned)result->version, (unsigned)result->shotId,
                    l3_result_verdict_name(result->verdict), (unsigned)result->validFlags,
                    (unsigned)result->qualityFlags, (unsigned)result->impactTimestampUs,
                    l3_shot_source_name(result->impactSource, sourceText, sizeof(sourceText)),
                    (unsigned)result->clubPoints, (unsigned)result->ballPoints, smashText);
}

int32_t l3_result_format_metric(const l3_shot_result_t *result, uint32_t index, char *out,
                                uint32_t cap)
{
    const l3_measurement_t *m;
    char valueText[16];
    char confidenceText[16];
    char flags[48];
    uint32_t angle;

    if (index >= L3_RESULT_METRICS) {
        return snprintf(out, cap, "metric ?");
    }
    m = &result->metric[index];
    angle = (index == L3_METRIC_VERTICAL_LAUNCH || index == L3_METRIC_HORIZONTAL_LAUNCH ||
             index == L3_METRIC_CLUB_PATH || index == L3_METRIC_ANGLE_OF_ATTACK ||
             index == L3_METRIC_SPIN_AXIS)
                ? 1U
                : 0U;
    if (angle) {
        l3_text_degrees2(m->value, valueText, sizeof(valueText));
    } else {
        l3_text_fixed2(m->value, valueText, sizeof(valueText));
    }
    l3_text_fixed2(m->confidence, confidenceText, sizeof(confidenceText));
    if (!(m->flags & L3_MEAS_VALID)) {
        (void)snprintf(flags, sizeof(flags), "none");
    } else {
        (void)snprintf(flags, sizeof(flags), "%s%s%s%s",
                       (m->flags & L3_MEAS_MEASURED) ? "measured" : "inferred",
                       (m->flags & L3_MEAS_RADIAL_ONLY) ? ",radial" : "",
                       (m->flags & L3_MEAS_IMPLAUSIBLE) ? ",implausible" : "",
                       (m->flags & L3_MEAS_FALLBACK) ? ",tee" : "");
    }
    return snprintf(out, cap, "  %s=%s%s conf=%s flags=%s", l3_result_metric_name(index),
                    (m->flags & L3_MEAS_VALID) ? valueText : "-",
                    (m->flags & L3_MEAS_VALID) ? (angle ? "deg" : "") : "", confidenceText,
                    flags);
}

int32_t l3_result_format_hex(const l3_shot_result_t *result, char *out, uint32_t cap)
{
    static const char kHex[] = "0123456789abcdef";
    uint8_t packet[L3_RESULT_PACKET_BYTES];
    uint32_t n = l3_result_serialize(result, packet, sizeof(packet));
    uint32_t i;

    if (cap < 2U * n + 1U) {
        if (cap > 0U) {
            out[0] = '\0';
        }
        return -1;
    }
    for (i = 0U; i < n; i++) {
        out[2U * i] = kHex[packet[i] >> 4];
        out[2U * i + 1U] = kHex[packet[i] & 0x0FU];
    }
    out[2U * n] = '\0';
    return (int32_t)(2U * n);
}
