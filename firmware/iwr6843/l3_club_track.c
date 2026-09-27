/* IWR6843 club track. See l3_club_track.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_club_track.h"

static const char *const kWhyNames[L3_TRACK_WHY_COUNT] = {
    "none", "acquired", "associated", "coasted", "dropped", "idle"
};

void l3_track_cfg_defaults(l3_track_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->binWidthM = 6.0F / 128.0F;
    cfg->gateBins = 3.0F;        /* a driver moves ~2.5 bins per 3 ms frame */
    cfg->maxMisses = 2U;
    cfg->minConfidence = 0.2F;
    cfg->weightRange = 1.0F;     /* one bin of range error ... */
    cfg->weightVelocity = 1.0F;  /* ... equals a full wrap of Doppler ... */
    cfg->weightQuality = 1.0F;   /* ... equals a target of no confidence */
    cfg->velocitySpanMps = 2.0F * L3_OBS_WAVELENGTH_M / (4.0F * 135.0e-6F);
}

void l3_track_init(l3_club_track_t *track, const l3_track_cfg_t *cfg)
{
    memset(track, 0, sizeof(*track));
    track->cfg = *cfg;
}

void l3_track_reset(l3_club_track_t *track)
{
    track->active = 0U;
    track->why = L3_TRACK_WHY_NONE;
    track->next = 0U;
    track->count = 0U;
    track->misses = 0U;
    track->lastFrame = 0U;
    track->lastBin = 0.0F;
    track->velocityBinsPerFrame = 0.0F;
    track->predictedBin = 0.0F;
}

static void l3_track_note(l3_club_track_t *track, uint8_t why)
{
    track->why = why;
    track->counters[why]++;
}

static void l3_track_append(l3_club_track_t *track, const l3_target_obs_t *target,
                            float velocityBinsPerFrame, float dtS)
{
    l3_track_point_t *point = &track->points[track->next];

    point->frame = target->frame;
    point->timestampUs = target->timestampUs;
    point->rangeBin = target->rangeBin;
    point->rangeM = target->rangeBin * track->cfg.binWidthM;
    point->radialVelocityMps = (dtS > 0.0F)
                                   ? (velocityBinsPerFrame * track->cfg.binWidthM / dtS)
                                   : 0.0F;
    point->dopplerAliasMps = target->dopplerAliasMps;
    point->azimuthRad = target->azimuthRad;
    point->elevationRad = target->elevationRad;
    point->anglesValid = target->anglesValid;
    point->energy = target->energy;
    point->coherence = target->coherence;
    point->confidence = target->confidence;
    track->next = (track->next + 1U) % L3_TRACK_POINTS;
    if (track->count < L3_TRACK_POINTS) {
        track->count++;
    }
    track->total++;
}

/* Doppler continuity: the smaller way round the alias circle. */
static float l3_track_wrappedDiff(float a, float b, float span)
{
    float diff = a - b;

    if (span <= 0.0F) {
        return 0.0F;
    }
    while (diff > 0.5F * span) {
        diff -= span;
    }
    while (diff < -0.5F * span) {
        diff += span;
    }
    return (diff < 0.0F) ? -diff : diff;
}

int32_t l3_track_update(l3_club_track_t *track, const l3_target_obs_t *targets, uint32_t n,
                        uint32_t frame, uint32_t timestampUs)
{
    const l3_track_cfg_t *cfg = &track->cfg;
    uint32_t i;

    if (!track->active) {
        /* Acquire the most confident target that clears the bar. */
        const l3_target_obs_t *best = NULL;
        for (i = 0U; i < n; i++) {
            if (targets[i].confidence >= cfg->minConfidence &&
                (best == NULL || targets[i].confidence > best->confidence)) {
                best = &targets[i];
            }
        }
        if (best == NULL) {
            l3_track_note(track, L3_TRACK_WHY_IDLE);
            return 0;
        }
        track->active = 1U;
        track->misses = 0U;
        track->lastFrame = frame;
        track->lastBin = best->rangeBin;
        track->velocityBinsPerFrame = 0.0F;
        track->predictedBin = best->rangeBin;
        l3_track_append(track, best, 0.0F, 0.0F);
        l3_track_note(track, L3_TRACK_WHY_ACQUIRED);
        return 1;
    }

    {
        uint32_t elapsed = frame - track->lastFrame;
        float predicted = track->lastBin + track->velocityBinsPerFrame * (float)elapsed;
        const l3_target_obs_t *best = NULL;
        float bestScore = 0.0F;
        const l3_track_point_t *last =
            &track->points[(track->next + L3_TRACK_POINTS - 1U) % L3_TRACK_POINTS];

        track->predictedBin = predicted;
        for (i = 0U; i < n; i++) {
            float rangeErr = targets[i].rangeBin - predicted;
            float velocityErr;
            float score;
            if (rangeErr < 0.0F) {
                rangeErr = -rangeErr;
            }
            if (rangeErr > cfg->gateBins) {
                continue;
            }
            velocityErr = l3_track_wrappedDiff(targets[i].dopplerAliasMps,
                                               last->dopplerAliasMps, cfg->velocitySpanMps) /
                          ((cfg->velocitySpanMps > 0.0F) ? cfg->velocitySpanMps : 1.0F);
            score = cfg->weightRange * rangeErr + cfg->weightVelocity * velocityErr +
                    cfg->weightQuality * (1.0F - targets[i].confidence);
            if (best == NULL || score < bestScore) {
                best = &targets[i];
                bestScore = score;
            }
        }
        if (best == NULL) {
            track->misses++;
            if (track->misses > cfg->maxMisses) {
                track->active = 0U;
                l3_track_note(track, L3_TRACK_WHY_DROPPED);
            } else {
                l3_track_note(track, L3_TRACK_WHY_COASTED);
            }
            return 0;
        }
        if (elapsed > 0U) {
            float measured = (best->rangeBin - track->lastBin) / (float)elapsed;
            float dtS = (float)(timestampUs - last->timestampUs) * 1.0e-6F;
            /* Half new, half old: smooth enough to predict with, quick enough
             * for a club that accelerates through the approach. */
            track->velocityBinsPerFrame = (track->count > 1U)
                                              ? 0.5F * (track->velocityBinsPerFrame + measured)
                                              : measured;
            l3_track_append(track, best, track->velocityBinsPerFrame, dtS / (float)elapsed);
        } else {
            l3_track_append(track, best, track->velocityBinsPerFrame, 0.0F);
        }
        track->misses = 0U;
        track->lastFrame = frame;
        track->lastBin = best->rangeBin;
        l3_track_note(track, L3_TRACK_WHY_ASSOCIATED);
        return 1;
    }
}

int32_t l3_track_point(const l3_club_track_t *track, uint32_t index, l3_track_point_t *out)
{
    uint32_t oldest;

    if (index >= track->count) {
        return 0;
    }
    oldest = (track->next + L3_TRACK_POINTS - track->count) % L3_TRACK_POINTS;
    *out = track->points[(oldest + index) % L3_TRACK_POINTS];
    return 1;
}

uint32_t l3_track_fit(const l3_club_track_t *track, uint32_t maxPoints, float *slopeBinsPerS,
                      float *residualBins)
{
    uint32_t used = (maxPoints < track->count) ? maxPoints : track->count;
    uint32_t first = track->count - used;
    uint32_t i;
    float t0 = 0.0F;
    float sumT = 0.0F;
    float sumB = 0.0F;
    float sumTT = 0.0F;
    float sumTB = 0.0F;
    float denominator;
    float slope;
    float intercept;
    float residual = 0.0F;
    l3_track_point_t point;

    *slopeBinsPerS = 0.0F;
    *residualBins = 0.0F;
    if (used < 3U) {
        return 0U;
    }
    (void)l3_track_point(track, first, &point);
    t0 = (float)point.timestampUs;
    for (i = first; i < track->count; i++) {
        float t;
        (void)l3_track_point(track, i, &point);
        t = ((float)point.timestampUs - t0) * 1.0e-6F;
        sumT += t;
        sumB += point.rangeBin;
        sumTT += t * t;
        sumTB += t * point.rangeBin;
    }
    denominator = (float)used * sumTT - sumT * sumT;
    if (denominator <= 0.0F) {
        return 0U;
    }
    slope = ((float)used * sumTB - sumT * sumB) / denominator;
    intercept = (sumB - slope * sumT) / (float)used;
    for (i = first; i < track->count; i++) {
        float t;
        float error;
        (void)l3_track_point(track, i, &point);
        t = ((float)point.timestampUs - t0) * 1.0e-6F;
        error = point.rangeBin - (intercept + slope * t);
        residual += error * error;
    }
    *slopeBinsPerS = slope;
    *residualBins = sqrtf(residual / (float)used);
    return used;
}

float l3_track_speed_mps(const l3_club_track_t *track, uint32_t maxPoints)
{
    float slope;
    float residual;

    if (l3_track_fit(track, maxPoints, &slope, &residual) == 0U) {
        return 0.0F;
    }
    return ((slope < 0.0F) ? -slope : slope) * track->cfg.binWidthM;
}

const char *l3_track_why_name(uint8_t why)
{
    return (why < L3_TRACK_WHY_COUNT) ? kWhyNames[why] : "?";
}

static void l3_track_fmt2(float value, char *out, uint32_t cap)
{
    const char *sign = (value < 0.0F) ? "-" : "";
    unsigned whole;
    unsigned hundredths;

    if (value < 0.0F) {
        value = -value;
    }
    if (value > 4.0e9F) {
        value = 4.0e9F;
    }
    whole = (unsigned)value;
    hundredths = (unsigned)((value - (float)whole) * 100.0F + 0.5F);
    if (hundredths >= 100U) {
        whole++;
        hundredths = 0U;
    }
    (void)snprintf(out, cap, "%s%u.%02u", sign, whole, hundredths);
}

int32_t l3_track_format_status(const l3_club_track_t *track, uint32_t destBin, char *out,
                               uint32_t cap)
{
    char binText[16];
    char distText[16];
    char velocityText[16];
    char speedText[16];
    char residualText[16];
    float slope = 0.0F;
    float residual = 0.0F;
    uint32_t fitted = l3_track_fit(track, 8U, &slope, &residual);

    l3_track_fmt2(track->lastBin, binText, sizeof(binText));
    l3_track_fmt2((float)destBin - track->lastBin, distText, sizeof(distText));
    l3_track_fmt2(track->velocityBinsPerFrame, velocityText, sizeof(velocityText));
    l3_track_fmt2(((slope < 0.0F) ? -slope : slope) * track->cfg.binWidthM, speedText,
                  sizeof(speedText));
    l3_track_fmt2(residual, residualText, sizeof(residualText));
    return snprintf(out, cap,
                    "clubtrack active=%u why=%s count=%u total=%u misses=%u bin=%s dest=%u "
                    "dist=%s vel=%s speed=%s fit=%u residual=%s acq=%u assoc=%u coast=%u "
                    "drop=%u",
                    (unsigned)track->active, l3_track_why_name(track->why),
                    (unsigned)track->count, (unsigned)track->total, (unsigned)track->misses,
                    binText, (unsigned)destBin, distText, velocityText, speedText,
                    (unsigned)fitted, residualText,
                    (unsigned)track->counters[L3_TRACK_WHY_ACQUIRED],
                    (unsigned)track->counters[L3_TRACK_WHY_ASSOCIATED],
                    (unsigned)track->counters[L3_TRACK_WHY_COASTED],
                    (unsigned)track->counters[L3_TRACK_WHY_DROPPED]);
}

int32_t l3_track_format_point(const l3_track_point_t *point, uint32_t destBin, char *out,
                              uint32_t cap)
{
    char binText[16];
    char distText[16];
    char rangeText[16];
    char velocityText[16];
    char dopplerText[16];
    char confidenceText[16];

    l3_track_fmt2(point->rangeBin, binText, sizeof(binText));
    l3_track_fmt2((float)destBin - point->rangeBin, distText, sizeof(distText));
    l3_track_fmt2(point->rangeM, rangeText, sizeof(rangeText));
    l3_track_fmt2(point->radialVelocityMps, velocityText, sizeof(velocityText));
    l3_track_fmt2(point->dopplerAliasMps, dopplerText, sizeof(dopplerText));
    l3_track_fmt2(point->confidence, confidenceText, sizeof(confidenceText));
    return snprintf(out, cap,
                    "p frame=%u t=%u bin=%s dist=%s range=%s vr=%s vd=%s coh=%u conf=%s "
                    "angles=%s",
                    (unsigned)point->frame, (unsigned)point->timestampUs, binText, distText,
                    rangeText, velocityText, dopplerText,
                    (unsigned)(point->coherence * 100.0F + 0.5F), confidenceText,
                    point->anglesValid ? "valid" : "none");
}
