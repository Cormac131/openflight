/* See l3_angle.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_angle.h"
#include "l3_text.h"

#define L3_ANGLE_ALIAS_SEARCH 3

static l3_cpx_t l3_angle_mul(l3_cpx_t a, l3_cpx_t b)
{
    l3_cpx_t out;

    out.re = a.re * b.re - a.im * b.im;
    out.im = a.re * b.im + a.im * b.re;
    return out;
}

static l3_cpx_t l3_angle_conj(l3_cpx_t a)
{
    a.im = -a.im;
    return a;
}

static l3_cpx_t l3_angle_phasor(float phaseRad)
{
    l3_cpx_t out;

    out.re = cosf(phaseRad);
    out.im = sinf(phaseRad);
    return out;
}

static float l3_angle_abs(l3_cpx_t a)
{
    return sqrtf(a.re * a.re + a.im * a.im);
}

static float l3_angle_wrap(float phase)
{
    while (phase > L3_ANGLE_PI) {
        phase -= 2.0F * L3_ANGLE_PI;
    }
    while (phase < -L3_ANGLE_PI) {
        phase += 2.0F * L3_ANGLE_PI;
    }
    return phase;
}

void l3_angle_snapshot_init(l3_angle_snapshot_t *snapshot, uint32_t ntx, uint32_t nrx)
{
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->ntx = (ntx > L3_ANGLE_MAX_TX) ? L3_ANGLE_MAX_TX : ntx;
    snapshot->nrx = (nrx > L3_ANGLE_MAX_RX) ? L3_ANGLE_MAX_RX : nrx;
    snapshot->chirpPeriodS = 45.0e-6F;
}

float l3_angle_chirp_phase(float lag1PhaseRad, uint32_t ntx, float radialVelocityMps,
                           float chirpPeriodS)
{
    float expected = 4.0F * L3_ANGLE_PI * radialVelocityMps * chirpPeriodS / L3_ANGLE_WAVELENGTH_M;
    float best = 0.0F;
    float bestError = -1.0F;
    int k;

    if (ntx == 0U) {
        return 0.0F;
    }
    for (k = -L3_ANGLE_ALIAS_SEARCH; k <= L3_ANGLE_ALIAS_SEARCH; k++) {
        float perLoop = lag1PhaseRad + 2.0F * L3_ANGLE_PI * (float)k;
        float candidate = perLoop / (float)ntx;
        float error = fabsf(candidate - expected);

        if (bestError < 0.0F || error < bestError) {
            bestError = error;
            best = candidate;
        }
    }
    return best;
}

float l3_angle_bartlett(const l3_cpx_t *elements, uint32_t n, float *peakRatio)
{
    float power[L3_ANGLE_GRID_STEPS];
    float total = 0.0F;
    float peak = -1.0F;
    uint32_t peakIndex = 0U;
    uint32_t step;
    float refined;

    if (n > L3_ANGLE_MAX_VIRTUAL) {
        n = L3_ANGLE_MAX_VIRTUAL;
    }
    for (step = 0U; step < L3_ANGLE_GRID_STEPS; step++) {
        float theta = ((float)step - (float)(L3_ANGLE_GRID_STEPS - 1U) / 2.0F) *
                      L3_ANGLE_GRID_STEP_RAD;
        float phaseStep = -L3_ANGLE_PI * sinf(theta);
        l3_cpx_t rotor = l3_angle_phasor(phaseStep);
        l3_cpx_t weight;
        l3_cpx_t sum;
        uint32_t m;

        weight.re = 1.0F;
        weight.im = 0.0F;
        sum.re = 0.0F;
        sum.im = 0.0F;
        for (m = 0U; m < n; m++) {
            l3_cpx_t term = l3_angle_mul(elements[m], weight);

            sum.re += term.re;
            sum.im += term.im;
            weight = l3_angle_mul(weight, rotor);
        }
        power[step] = sum.re * sum.re + sum.im * sum.im;
        total += power[step];
        if (power[step] > peak) {
            peak = power[step];
            peakIndex = step;
        }
    }
    if (peakRatio != NULL) {
        float mean = total / (float)L3_ANGLE_GRID_STEPS;

        *peakRatio = (mean > 0.0F) ? (peak / mean) : 0.0F;
    }
    refined = (float)peakIndex;
    if (peakIndex > 0U && peakIndex < L3_ANGLE_GRID_STEPS - 1U) {
        float left = power[peakIndex - 1U];
        float right = power[peakIndex + 1U];
        float denominator = left - 2.0F * peak + right;

        if (denominator < 0.0F) {
            refined += 0.5F * (left - right) / denominator;
        }
    }
    return (refined - (float)(L3_ANGLE_GRID_STEPS - 1U) / 2.0F) * L3_ANGLE_GRID_STEP_RAD;
}

float l3_angle_confidence(float elevationPeakRatio, float azimuthCoherence, uint8_t azimuthValid)
{
    float elevation = (elevationPeakRatio - 1.0F) / (L3_ANGLE_PEAK_RATIO_FULL - 1.0F);
    float confidence;

    if (elevation < 0.0F) {
        elevation = 0.0F;
    } else if (elevation > 1.0F) {
        elevation = 1.0F;
    }
    confidence = elevation;
    if (azimuthValid) {
        float azimuth = azimuthCoherence;

        if (azimuth < 0.0F) {
            azimuth = 0.0F;
        } else if (azimuth > 1.0F) {
            azimuth = 1.0F;
        }
        if (azimuth < confidence) {
            confidence = azimuth;
        }
    }
    return confidence;
}

int32_t l3_angle_estimate(const l3_radar_cal_t *cal, const l3_angle_snapshot_t *snapshot,
                          l3_angle_obs_t *out)
{
    l3_cpx_t corrected[L3_ANGLE_MAX_CHANNELS];
    l3_cpx_t elements[L3_ANGLE_MAX_VIRTUAL];
    uint32_t ntx = snapshot->ntx;
    uint32_t nrx = snapshot->nrx;
    uint32_t txA = 0U;
    uint32_t txB = (ntx == 3U) ? 2U : 1U;
    uint32_t n;
    uint32_t tx;
    uint32_t rx;
    uint32_t m;

    memset(out, 0, sizeof(*out));
    if (ntx < 2U || ntx > L3_ANGLE_MAX_TX || nrx < 2U || nrx > L3_ANGLE_MAX_RX) {
        return 0;
    }
    out->chirpPhaseRad = l3_angle_chirp_phase(snapshot->lag1PhaseRad, ntx,
                                              snapshot->radialVelocityMps,
                                              snapshot->chirpPeriodS);
    /* Every TX block back to the loop's first chirp. */
    for (tx = 0U; tx < ntx; tx++) {
        l3_cpx_t undo = l3_angle_phasor(-out->chirpPhaseRad * (float)tx);

        for (rx = 0U; rx < nrx; rx++) {
            corrected[tx * nrx + rx] = l3_angle_mul(snapshot->channel[tx * nrx + rx], undo);
        }
    }
    /* Elevation: [txA.rx0.., txB.rx0..] flipped into physical order, corrected. */
    n = 2U * nrx;
    for (m = 0U; m < n; m++) {
        uint32_t logical = n - 1U - m;
        uint32_t txIndex = (logical < nrx) ? txA : txB;
        l3_cpx_t value = corrected[txIndex * nrx + (logical % nrx)];

        if (m < cal->virtualElements) {
            l3_cpx_t correction;

            correction.re = cal->correctionRe[m];
            correction.im = cal->correctionIm[m];
            value = l3_angle_mul(value, correction);
        }
        elements[m] = value;
    }
    out->elevationRad = l3_angle_bartlett(elements, n, &out->elevationPeakRatio) -
                        cal->elevationOffsetRad;
    out->elevationValid = 1U;
    /* Azimuth: TX1 against the centre of the vertical pair, coherent over RX. */
    if (ntx == 3U) {
        l3_cpx_t mean;
        float phase;
        float sine;

        mean.re = 0.0F;
        mean.im = 0.0F;
        for (rx = 0U; rx < nrx; rx++) {
            l3_cpx_t reference;
            l3_cpx_t product;
            float magnitude;

            reference.re = 0.5F * (corrected[txA * nrx + rx].re + corrected[txB * nrx + rx].re);
            reference.im = 0.5F * (corrected[txA * nrx + rx].im + corrected[txB * nrx + rx].im);
            product = l3_angle_mul(l3_angle_conj(reference), corrected[1U * nrx + rx]);
            magnitude = l3_angle_abs(product);
            if (magnitude > 0.0F) {
                mean.re += product.re / magnitude;
                mean.im += product.im / magnitude;
            }
        }
        out->azimuthCoherence = l3_angle_abs(mean) / (float)nrx;
        if (out->azimuthCoherence > 0.0F) {
            phase = l3_angle_wrap(atan2f(mean.im, mean.re) - cal->azimuthOffsetRad);
            sine = phase / (2.0F * L3_ANGLE_PI * L3_ANGLE_BASELINE_LAMBDA);
            if (sine > 1.0F) {
                sine = 1.0F;
            } else if (sine < -1.0F) {
                sine = -1.0F;
            }
            /* TX1 is physically left of the centre: a target to the right
             * reads a negative phase, and azimuth is positive right. */
            out->azimuthRad = -asinf(sine);
            out->azimuthValid = 1U;
        }
    }
    out->confidence =
        l3_angle_confidence(out->elevationPeakRatio, out->azimuthCoherence, out->azimuthValid);
    return 1;
}

int32_t l3_angle_format(const l3_angle_obs_t *obs, char *out, uint32_t cap)
{
    char azText[16];
    char elText[16];
    char cohText[16];
    char peakText[16];
    char psiText[16];
    char confText[16];
    char valid[3];
    uint32_t v = 0U;

    l3_text_degrees2(obs->azimuthRad, azText, sizeof(azText));
    l3_text_degrees2(obs->elevationRad, elText, sizeof(elText));
    l3_text_fixed2(obs->azimuthCoherence, cohText, sizeof(cohText));
    l3_text_fixed(obs->elevationPeakRatio, 1U, peakText, sizeof(peakText));
    l3_text_fixed2(obs->chirpPhaseRad, psiText, sizeof(psiText));
    l3_text_fixed2(obs->confidence, confText, sizeof(confText));
    if (obs->azimuthValid) {
        valid[v++] = 'a';
    }
    if (obs->elevationValid) {
        valid[v++] = 'e';
    }
    valid[v] = '\0';
    return snprintf(out, cap, "angle az=%s el=%s coh=%s peak=%s psi=%s conf=%s valid=%s", azText,
                    elText, cohText, peakText, psiText, confText, (v > 0U) ? valid : "none");
}
