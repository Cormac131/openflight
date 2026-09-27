/* IWR6843 radar observation layer. See l3_observation.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_observation.h"
#include "l3_text.h"

float l3_obs_stat(uint32_t stat, const l3_bin_obs_t *obs)
{
    return (stat == L3_OBS_STAT_PEAK) ? obs->peak : obs->energy;
}

float l3_obs_median(uint32_t stat, const l3_bin_obs_t *obs, uint32_t count)
{
    /* Static: the caller's task stack is small and only one task scores
     * frames. Not reentrant. Insertion sort: the region is a dozen or so
     * bins, so this is cheaper than anything cleverer. */
    static float sorted[L3_OBS_MAX_BINS];
    uint32_t i;

    if (count == 0U) {
        return 0.0F;
    }
    if (count > L3_OBS_MAX_BINS) {
        count = L3_OBS_MAX_BINS;
    }
    for (i = 0U; i < count; i++) {
        float value = l3_obs_stat(stat, &obs[i]);
        uint32_t j = i;
        while (j > 0U && sorted[j - 1U] > value) {
            sorted[j] = sorted[j - 1U];
            j--;
        }
        sorted[j] = value;
    }
    if ((count & 1U) != 0U) {
        return sorted[count / 2U];
    }
    return 0.5F * (sorted[count / 2U - 1U] + sorted[count / 2U]);
}

void l3_obs_floor_update(float *floor, uint32_t stat, const l3_bin_obs_t *obs,
                         uint32_t count, uint32_t shift)
{
    float median = l3_obs_median(stat, obs, count);

    if (*floor <= 0.0F) {
        *floor = median;
    } else {
        *floor += (median - *floor) / (float)(1U << shift);
    }
    if (*floor < L3_OBS_FLOOR_MIN) {
        *floor = L3_OBS_FLOOR_MIN;
    }
}

float l3_obs_velocity(float r1Re, float r1Im, float loopPeriodS)
{
    if (loopPeriodS <= 0.0F || (r1Re == 0.0F && r1Im == 0.0F)) {
        return 0.0F;
    }
    return atan2f(r1Im, r1Re) * L3_OBS_WAVELENGTH_M / (4.0F * L3_OBS_PI * loopPeriodS);
}

static void l3_obs_fill(const l3_obs_params_t *params, uint32_t frame, uint32_t timestampUs,
                        uint32_t firstBin, const l3_bin_obs_t *obs, uint32_t count,
                        uint32_t index, float floor, float threshold, l3_target_obs_t *target)
{
    const l3_bin_obs_t *bin = &obs[index];
    float stat = l3_obs_stat(params->stat, bin);
    float weight = stat;
    float moment = stat * (float)(firstBin + index);
    float magnitude;
    float margin;

    memset(target, 0, sizeof(*target));
    target->frame = frame;
    target->timestampUs = timestampUs;
    target->peakBin = (uint8_t)(firstBin + index);
    /* Sub-bin range: the statistic's centroid over the peak and its
     * neighbours, above the floor so noise does not pull it. */
    if (index > 0U) {
        float side = l3_obs_stat(params->stat, &obs[index - 1U]) - floor;
        if (side > 0.0F) {
            weight += side;
            moment += side * (float)(firstBin + index - 1U);
        }
    }
    if (index + 1U < count) {
        float side = l3_obs_stat(params->stat, &obs[index + 1U]) - floor;
        if (side > 0.0F) {
            weight += side;
            moment += side * (float)(firstBin + index + 1U);
        }
    }
    target->rangeBin = (weight > 0.0F) ? (moment / weight) : (float)(firstBin + index);
    target->energy = bin->energy;
    target->peak = bin->peak;
    target->loop0 = bin->loop0;
    target->stat = stat;
    target->snr = stat / ((floor > 0.0F) ? floor : L3_OBS_FLOOR_MIN);
    target->r1Re = bin->r1Re;
    target->r1Im = bin->r1Im;
    if (bin->energy > 0.0F) {
        magnitude = sqrtf(bin->r1Re * bin->r1Re + bin->r1Im * bin->r1Im);
        target->coherence = magnitude / bin->energy;
        if (target->coherence > 1.0F) {
            target->coherence = 1.0F;
        }
        target->dopplerPhaseRad = atan2f(bin->r1Im, bin->r1Re);
    }
    target->dopplerAliasMps = l3_obs_velocity(bin->r1Re, bin->r1Im, params->loopPeriodS);
    target->anglesValid = 0U;
    /* Confidence: how far the statistic clears the threshold (full at 3x
     * over it), tempered by coherence (an incoherent rise is half a target). */
    margin = (stat - threshold) / (3.0F * threshold);
    if (margin > 1.0F) {
        margin = 1.0F;
    }
    if (margin < 0.0F) {
        margin = 0.0F;
    }
    target->confidence = margin * (0.5F + 0.5F * target->coherence);
}

uint32_t l3_obs_extract(const l3_obs_params_t *params, uint32_t frame, uint32_t timestampUs,
                        uint32_t firstBin, const l3_bin_obs_t *obs, uint32_t count,
                        float floor, l3_target_obs_t *out, uint32_t maxOut)
{
    float threshold = ((floor > 0.0F) ? floor : L3_OBS_FLOOR_MIN) * params->snr;
    uint32_t written = 0U;
    uint32_t i;

    if (count > L3_OBS_MAX_BINS) {
        count = L3_OBS_MAX_BINS;
    }
    for (i = 0U; i < count; i++) {
        float stat = l3_obs_stat(params->stat, &obs[i]);
        l3_target_obs_t target;
        uint32_t slot;

        if (stat < threshold) {
            continue;
        }
        /* Local maximum: a plateau's first bin counts, its followers do not. */
        if (i > 0U && l3_obs_stat(params->stat, &obs[i - 1U]) >= stat) {
            continue;
        }
        if (i + 1U < count && l3_obs_stat(params->stat, &obs[i + 1U]) > stat) {
            continue;
        }
        l3_obs_fill(params, frame, timestampUs, firstBin, obs, count, i, floor, threshold, &target);
        /* Insert strongest first; drop the weakest when full. */
        slot = written;
        while (slot > 0U && out[slot - 1U].stat < target.stat) {
            if (slot < maxOut) {
                out[slot] = out[slot - 1U];
            }
            slot--;
        }
        if (slot < maxOut) {
            out[slot] = target;
            if (written < maxOut) {
                written++;
            }
        }
    }
    return written;
}

int32_t l3_obs_format_target(const l3_target_obs_t *target, char *out, uint32_t cap)
{
    char rangeText[16];
    char snrText[16];
    char velocityText[16];
    char confidenceText[16];
    char energyText[16];
    char peakText[16];

    l3_text_fixed(target->rangeBin, 2U, rangeText, sizeof(rangeText));
    l3_text_fixed(target->snr, 1U, snrText, sizeof(snrText));
    l3_text_fixed(target->dopplerAliasMps, 2U, velocityText, sizeof(velocityText));
    l3_text_fixed(target->confidence, 2U, confidenceText, sizeof(confidenceText));
    l3_text_fixed(target->energy, 0U, energyText, sizeof(energyText));
    l3_text_fixed(target->peak, 0U, peakText, sizeof(peakText));
    return snprintf(out, cap,
                    "obs frame=%u bin=%u range=%s snr=%s energy=%s peak=%s coh=%u v=%s "
                    "conf=%s angles=%s",
                    (unsigned)target->frame, (unsigned)target->peakBin, rangeText, snrText,
                    energyText, peakText, (unsigned)(target->coherence * 100.0F + 0.5F),
                    velocityText, confidenceText, target->anglesValid ? "valid" : "none");
}
