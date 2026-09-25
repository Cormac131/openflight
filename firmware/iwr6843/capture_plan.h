/* Capture-plan arithmetic, shared by the R4F build and the host tests.
 *
 * Pure C99 with no TI headers, so tests/test_iwr6843_capture_plan.py can
 * compile this file with the host compiler. l3_dump.c owns the globals and
 * the CLI; this file owns the byte budget and the per-frame layout.
 */
#ifndef L3_CAPTURE_PLAN_H
#define L3_CAPTURE_PLAN_H

#include <stdint.h>

typedef struct {
    uint8_t  preStart;
    uint8_t  preBins;
    uint8_t  postStart;
    uint8_t  postBins;
    uint8_t  lateStart;
    uint8_t  postFrames;
    uint8_t  postStride;
    uint8_t  preFrames;
    uint8_t  totalFrames;
    uint16_t loops;
    uint16_t chirpsPerFrame;
    uint32_t preFrameBytes;
    uint32_t postFrameBytes;
    uint32_t postBaseOffset;
    uint32_t usedBytes;
    uint8_t  phased;
    uint8_t  requestedPreFrames;
    uint8_t  impactStart;
    uint8_t  impactBins;
    uint8_t  impactFrames;
    uint8_t  ballFrames;
    uint32_t impactFrameBytes;
} L3CapturePlan;

typedef struct {
    uint32_t nTx;
    uint32_t nRx;
    uint32_t maxSamples;
    uint32_t maxCaptureFrames;
    uint32_t maxLoops;
    uint32_t minLoops;
    uint32_t maxBins;
    uint32_t maxPostStride;
} L3CaptureGeometry;

typedef struct {
    uint8_t  *binStart;
    uint8_t  *binCount;
    uint16_t *deltaUs;
    uint32_t *offset;
    uint32_t *bytes;
} L3CaptureTables;

/* Returns 0 and fills plan + tables, or -1 with a message written to err. */
int32_t l3plan_build(L3CapturePlan *plan,
                     const L3CaptureGeometry *geom,
                     L3CaptureTables *tables,
                     uint32_t loops,
                     uint32_t framePeriodUs,
                     uint32_t capacityBytes,
                     uint32_t bytesPerComplex,
                     char *err,
                     uint32_t errLen);

/* Host-test only: lets ctypes verify its Plan mirror matches this struct's
 * actual compiled layout instead of assuming natural alignment agrees. */
uint32_t l3plan_sizeof_plan(void);

#endif /* L3_CAPTURE_PLAN_H */
