#include "capture_plan.h"

#include <stdio.h>

uint32_t l3plan_sizeof_plan(void)
{
    return (uint32_t)sizeof(L3CapturePlan);
}

int32_t l3plan_build(L3CapturePlan *plan,
                     const L3CaptureGeometry *geom,
                     L3CaptureTables *tables,
                     uint32_t loops,
                     uint32_t framePeriodUs,
                     uint32_t capacityBytes,
                     uint32_t bytesPerComplex,
                     char *err,
                     uint32_t errLen)
{
    uint32_t bytesPerBin;
    uint32_t captureBytes;
    uint32_t postBytes;
    uint32_t remaining;
    uint32_t preFrames;
    uint32_t frame;
    uint32_t cursor;

    if (loops < geom->minLoops || loops > geom->maxLoops || (loops & 1U) != 0U) {
        (void)snprintf(err, errLen, "Error: loops must be even and between %u and %u\n",
                       (unsigned)geom->minLoops, (unsigned)geom->maxLoops);
        return -1;
    }
    if (bytesPerComplex == 2U &&
        (plan->preBins > geom->maxBins ||
         plan->postBins > geom->maxBins ||
         (plan->phased &&
          plan->impactBins > geom->maxBins))) {
        (void)snprintf(err, errLen, "Error: iq8 capture windows cannot exceed %u bins\n",
                       (unsigned)geom->maxBins);
        return -1;
    }
    if (plan->preBins == 0U || plan->postBins == 0U ||
        plan->postFrames == 0U ||
        plan->postStride == 0U ||
        plan->postStride > geom->maxPostStride ||
        plan->postFrames >= geom->maxCaptureFrames ||
        framePeriodUs == 0U ||
        (framePeriodUs * plan->postStride) > 0xFFFFU ||
        ((uint32_t)plan->preStart + plan->preBins) > geom->maxSamples ||
        ((uint32_t)plan->postStart + plan->postBins) > geom->maxSamples ||
        ((uint32_t)plan->lateStart + plan->postBins) > geom->maxSamples ||
        (plan->phased &&
         (plan->requestedPreFrames == 0U ||
          plan->impactBins == 0U ||
          plan->impactFrames == 0U ||
          plan->ballFrames == 0U ||
          ((uint32_t)plan->impactStart + plan->impactBins) > geom->maxSamples ||
          ((uint32_t)plan->requestedPreFrames +
           plan->postFrames) > geom->maxCaptureFrames))) {
        (void)snprintf(err, errLen, "Error: captureCfg needs valid windows and 1-%u post frames\n",
                       (unsigned)(geom->maxCaptureFrames - 1U));
        return -1;
    }

    plan->loops = (uint16_t)loops;
    plan->chirpsPerFrame = (uint16_t)(geom->nTx * loops);
    captureBytes = capacityBytes;
    bytesPerBin = (uint32_t)plan->chirpsPerFrame *
                  geom->nRx * bytesPerComplex;
    plan->preFrameBytes = bytesPerBin * plan->preBins;
    plan->postFrameBytes = bytesPerBin * plan->postBins;
    plan->impactFrameBytes = bytesPerBin * plan->impactBins;
    postBytes = plan->phased
                    ? (plan->impactFrameBytes * plan->impactFrames) +
                      (plan->postFrameBytes * plan->ballFrames)
                    : plan->postFrameBytes * plan->postFrames;
    if (postBytes >= captureBytes) {
        (void)snprintf(err, errLen, "Error: post-trigger capture needs %u bytes; L3 has %u\n",
                       (unsigned)postBytes, (unsigned)captureBytes);
        return -1;
    }
    remaining = captureBytes - postBytes;
    preFrames = plan->phased
                    ? plan->requestedPreFrames
                    : remaining / plan->preFrameBytes;
    if (preFrames == 0U) {
        (void)snprintf(err, errLen, "Error: capture plan leaves no pre-trigger frame\n");
        return -1;
    }
    if (!plan->phased &&
        preFrames + plan->postFrames > geom->maxCaptureFrames) {
        preFrames = geom->maxCaptureFrames - plan->postFrames;
    }
    if (preFrames == 0U ||
        preFrames * plan->preFrameBytes > remaining) {
        (void)snprintf(err, errLen, "Error: capture plan exceeds L3 after pre-trigger reservation\n");
        return -1;
    }

    plan->preFrames = (uint8_t)preFrames;
    plan->totalFrames =
        (uint8_t)(preFrames + plan->postFrames);
    plan->postBaseOffset = preFrames * plan->preFrameBytes;
    cursor = 0U;

    for (frame = 0U; frame < preFrames; frame++) {
        tables->offset[frame] = cursor;
        tables->binStart[frame] = plan->preStart;
        tables->binCount[frame] = plan->preBins;
        tables->deltaUs[frame] = (uint16_t)framePeriodUs;
        tables->bytes[frame] = plan->preFrameBytes;
        cursor += plan->preFrameBytes;
    }

    if (plan->phased) {
        for (frame = 0U; frame < plan->impactFrames; frame++) {
            uint32_t slot = preFrames + frame;
            tables->offset[slot] = cursor;
            tables->binStart[slot] = plan->impactStart;
            tables->binCount[slot] = plan->impactBins;
            tables->deltaUs[slot] = (uint16_t)framePeriodUs;
            tables->bytes[slot] = plan->impactFrameBytes;
            cursor += plan->impactFrameBytes;
        }
        for (frame = 0U; frame < plan->ballFrames; frame++) {
            uint32_t slot = preFrames + plan->impactFrames + frame;
            tables->offset[slot] = cursor;
            tables->binStart[slot] =
                (frame < (plan->ballFrames / 2U))
                    ? plan->postStart : plan->lateStart;
            tables->binCount[slot] = plan->postBins;
            tables->deltaUs[slot] =
                (frame == 0U)
                    ? (uint16_t)framePeriodUs
                    : (uint16_t)(framePeriodUs * plan->postStride);
            tables->bytes[slot] = plan->postFrameBytes;
            cursor += plan->postFrameBytes;
        }
    } else {
        for (frame = 0U; frame < plan->postFrames; frame++) {
            uint32_t slot = preFrames + frame;
            tables->offset[slot] = cursor;
            tables->binStart[slot] =
                (frame < (plan->postFrames / 2U))
                    ? plan->postStart : plan->lateStart;
            tables->binCount[slot] = plan->postBins;
            tables->deltaUs[slot] =
                (frame == 0U)
                    ? (uint16_t)framePeriodUs
                    : (uint16_t)(framePeriodUs * plan->postStride);
            tables->bytes[slot] = plan->postFrameBytes;
            cursor += plan->postFrameBytes;
        }
    }
    plan->usedBytes = cursor;

    return 0;
}
