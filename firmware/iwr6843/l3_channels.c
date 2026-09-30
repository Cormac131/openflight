/* IWR6843 per-channel loops over a detect frame. See l3_channels.h. */
#include "l3_channels.h"

#include <math.h>
#include <stddef.h>
#include <string.h>

#include "l3_bin_score.h"

int32_t l3_channels_valid(const l3_channel_frame_t *frame, uint32_t localBin)
{
    return (frame != NULL && frame->base != NULL && frame->loops > 0U &&
            frame->loops <= L3_CHANNELS_MAX_LOOPS && frame->ntx > 0U &&
            frame->ntx <= L3_CHANNELS_MAX_TX && frame->nrx > 0U &&
            frame->nrx <= L3_CHANNELS_MAX_RX && (frame->cb == 1U || frame->cb == 2U) &&
            localBin < frame->binCount)
               ? 1
               : 0;
}

static float l3_channels_component(const uint8_t *component, uint32_t cb)
{
    if (cb == 1U) {
        return (float)*(const int8_t *)component;
    }
    return (float)*(const int16_t *)(const void *)component;
}

/* The same (tx, rx) one loop later: ntx chirps on, each nrx x binCount
 * complex samples of two components. */
static uint32_t l3_channels_loop_stride(const l3_channel_frame_t *frame)
{
    return frame->ntx * frame->nrx * frame->binCount * 2U * frame->cb;
}

static const uint8_t *l3_channels_first(const l3_channel_frame_t *frame, uint32_t tx,
                                        uint32_t rx, uint32_t localBin)
{
    return &frame->base[((tx * frame->nrx + rx) * frame->binCount + localBin) * 2U * frame->cb];
}

static int32_t l3_channels_vertical(const l3_channel_frame_t *frame, uint32_t tx)
{
    return (frame->ntx == 3U && tx == 1U) ? 0 : 1; /* TX1 of three: the azimuth element */
}

/* One channel's loops read once: the raw components, in loop order. */
static void l3_channels_read(const l3_channel_frame_t *frame, const uint8_t *first,
                             uint32_t loopStride, float *im, float *re)
{
    const uint8_t *sample = first;
    uint32_t loop;

    for (loop = 0U; loop < frame->loops; loop++) {
        im[loop] = l3_channels_component(sample, frame->cb);
        re[loop] = l3_channels_component(sample + frame->cb, frame->cb);
        sample += loopStride;
    }
}

void l3_channels_residual(const l3_channel_frame_t *frame, uint32_t localBin, float *perLoop,
                          l3_bin_obs_t *obs)
{
    float loopPower[L3_CHANNELS_MAX_LOOPS];
    float energy = 0.0F;
    float peak = 0.0F;
    float r1Re = 0.0F;
    float r1Im = 0.0F;
    uint32_t loopStride;
    uint32_t loop;
    uint32_t tx;

    if (!l3_channels_valid(frame, localBin)) {
        if (obs != NULL) {
            memset(obs, 0, sizeof(*obs));
        }
        return;
    }
    if (frame->cb == 2U) {
        /* IQ16: exact integer statistics, the code the DSS runs too, so the
         * two cores' answers agree bit for bit. */
        l3_bin_obs_t scored = {0.0F, 0.0F, 0.0F, 0.0F, 0.0F};

        (void)l3_bin_score_iq16((const int16_t *)(const void *)frame->base, frame->binCount,
                                localBin, frame->ntx, frame->nrx, frame->loops, &scored,
                                perLoop);
        if (obs != NULL) {
            *obs = scored;
        }
        return;
    }
    loopStride = l3_channels_loop_stride(frame);
    for (loop = 0U; loop < frame->loops; loop++) {
        loopPower[loop] = 0.0F;
    }
    for (tx = 0U; tx < frame->ntx; tx++) {
        uint32_t rx;

        if (!l3_channels_vertical(frame, tx)) {
            continue;
        }
        for (rx = 0U; rx < frame->nrx; rx++) {
            float xIm[L3_CHANNELS_MAX_LOOPS];
            float xRe[L3_CHANNELS_MAX_LOOPS];
            float meanIm = 0.0F;
            float meanRe = 0.0F;
            float prevIm = 0.0F;
            float prevRe = 0.0F;

            l3_channels_read(frame, l3_channels_first(frame, tx, rx, localBin), loopStride, xIm,
                             xRe);
            for (loop = 0U; loop < frame->loops; loop++) {
                meanIm += xIm[loop];
                meanRe += xRe[loop];
            }
            meanIm /= (float)frame->loops;
            meanRe /= (float)frame->loops;
            for (loop = 0U; loop < frame->loops; loop++) {
                float im = (xIm[loop] - meanIm) * frame->scale;
                float re = (xRe[loop] - meanRe) * frame->scale;
                float power = im * im + re * re;

                energy += power;
                loopPower[loop] += power;
                if (loop > 0U) {
                    /* residual[loop] * conj(residual[loop - 1]) */
                    r1Re += re * prevRe + im * prevIm;
                    r1Im += im * prevRe - re * prevIm;
                }
                prevIm = im;
                prevRe = re;
            }
        }
    }
    for (loop = 0U; loop < frame->loops; loop++) {
        if (perLoop != NULL) {
            perLoop[loop] = loopPower[loop];
        }
        if (loopPower[loop] > peak) {
            peak = loopPower[loop];
        }
    }
    if (obs != NULL) {
        obs->energy = energy;
        obs->peak = peak;
        obs->loop0 = loopPower[0];
        obs->r1Re = r1Re;
        obs->r1Im = r1Im;
    }
}

float l3_channels_static_power(const l3_channel_frame_t *frame, uint32_t localBin,
                               uint32_t loopStep)
{
    float total = 0.0F;
    uint32_t samples = 0U;
    uint32_t loopStride;
    uint32_t tx;

    if (!l3_channels_valid(frame, localBin) || loopStep == 0U) {
        return 0.0F;
    }
    loopStride = l3_channels_loop_stride(frame) * loopStep;
    for (tx = 0U; tx < frame->ntx; tx++) {
        uint32_t rx;

        if (!l3_channels_vertical(frame, tx)) {
            continue;
        }
        for (rx = 0U; rx < frame->nrx; rx++) {
            const uint8_t *sample = l3_channels_first(frame, tx, rx, localBin);
            uint32_t loop;

            /* loopStep > 1 subsamples the loops: a static target does not
             * change between them. */
            for (loop = 0U; loop < frame->loops; loop += loopStep) {
                float im = l3_channels_component(sample, frame->cb) * frame->scale;
                float re = l3_channels_component(sample + frame->cb, frame->cb) * frame->scale;

                total += im * im + re * re;
                sample += loopStride;
                samples++;
            }
        }
    }
    return (samples > 0U) ? (total / (float)samples) : 0.0F;
}

static void l3_channels_snapshot_begin(const l3_channel_frame_t *frame,
                                       l3_angle_snapshot_t *out)
{
    l3_angle_snapshot_init(out, frame->ntx, frame->nrx);
    out->chirpPeriodS = frame->loopPeriodS / (float)frame->ntx;
}

void l3_channels_snapshot(const l3_channel_frame_t *frame, uint32_t localBin, float lag1PhaseRad,
                          float radialVelocityMps, l3_angle_snapshot_t *out)
{
    float stepRe;
    float stepIm;
    uint32_t loopStride;
    uint32_t tx;

    memset(out, 0, sizeof(*out));
    if (!l3_channels_valid(frame, localBin)) {
        return;
    }
    stepRe = cosf(lag1PhaseRad);
    stepIm = -sinf(lag1PhaseRad);
    loopStride = l3_channels_loop_stride(frame);
    l3_channels_snapshot_begin(frame, out);
    out->lag1PhaseRad = lag1PhaseRad;
    out->radialVelocityMps = radialVelocityMps;
    for (tx = 0U; tx < out->ntx; tx++) {
        uint32_t rx;

        for (rx = 0U; rx < out->nrx; rx++) {
            float xIm[L3_CHANNELS_MAX_LOOPS];
            float xRe[L3_CHANNELS_MAX_LOOPS];
            float meanIm = 0.0F;
            float meanRe = 0.0F;
            float sumRe = 0.0F;
            float sumIm = 0.0F;
            float rotRe = 1.0F; /* exp(-j * loop * lag1) */
            float rotIm = 0.0F;
            uint32_t loop;

            l3_channels_read(frame, l3_channels_first(frame, tx, rx, localBin), loopStride, xIm,
                             xRe);
            for (loop = 0U; loop < frame->loops; loop++) {
                meanIm += xIm[loop];
                meanRe += xRe[loop];
            }
            meanIm /= (float)frame->loops;
            meanRe /= (float)frame->loops;
            for (loop = 0U; loop < frame->loops; loop++) {
                float im = (xIm[loop] - meanIm) * frame->scale;
                float re = (xRe[loop] - meanRe) * frame->scale;
                float nextRe = rotRe * stepRe - rotIm * stepIm;
                float nextIm = rotRe * stepIm + rotIm * stepRe;

                sumRe += re * rotRe - im * rotIm;
                sumIm += re * rotIm + im * rotRe;
                rotRe = nextRe;
                rotIm = nextIm;
            }
            out->channel[tx * out->nrx + rx].re = sumRe;
            out->channel[tx * out->nrx + rx].im = sumIm;
        }
    }
}

void l3_channels_snapshot_static(const l3_channel_frame_t *frame, uint32_t localBin,
                                 l3_angle_snapshot_t *out)
{
    uint32_t loopStride;
    uint32_t tx;

    memset(out, 0, sizeof(*out));
    if (!l3_channels_valid(frame, localBin)) {
        return;
    }
    loopStride = l3_channels_loop_stride(frame);
    l3_channels_snapshot_begin(frame, out);
    for (tx = 0U; tx < out->ntx; tx++) {
        uint32_t rx;

        for (rx = 0U; rx < out->nrx; rx++) {
            const uint8_t *sample = l3_channels_first(frame, tx, rx, localBin);
            float sumRe = 0.0F;
            float sumIm = 0.0F;
            uint32_t loop;

            for (loop = 0U; loop < frame->loops; loop++) {
                sumIm += l3_channels_component(sample, frame->cb) * frame->scale;
                sumRe += l3_channels_component(sample + frame->cb, frame->cb) * frame->scale;
                sample += loopStride;
            }
            out->channel[tx * out->nrx + rx].re = sumRe;
            out->channel[tx * out->nrx + rx].im = sumIm;
        }
    }
}
