/* MSS <-> DSS solve protocol.
 *
 * The MSS freezes a capture and asks the DSS to solve it. The DSS reads the
 * capture arena in place from L3 and replies with a result record, or with
 * L3_SOLVE_NO_CONFIDENCE so the MSS can fall back to transferring cells.
 */
#ifndef L3_SOLVE_IPC_H
#define L3_SOLVE_IPC_H

#include <stdint.h>

#define L3_SOLVE_MAGIC        0x4C534F31U   /* "LSO1" */
#define L3_SOLVE_OK           0U
#define L3_SOLVE_NO_CONFIDENCE 1U
#define L3_SOLVE_ERROR        2U

typedef struct {
    uint32_t magic;
    uint32_t arenaAddr;      /* L3 address of frame 0 */
    uint32_t totalFrames;
    uint32_t loops;
    uint32_t nTx;
    uint32_t nRx;
    uint32_t framePeriodUs;
    uint32_t bytesPerComplex;
    uint32_t binStartsAddr;  /* per-frame tables, in MSS memory */
    uint32_t binCountsAddr;
    uint32_t frameOffsetsAddr;
    float    ballSpeedMph;
    float    rangeResM;
    float    tiltRad;
    float    teeRangeM;
} L3SolveRequest;

typedef struct {
    uint32_t magic;
    uint32_t status;         /* L3_SOLVE_OK / NO_CONFIDENCE / ERROR */
    float    launchAngleDeg;
    float    rawAngleDeg;
    float    clubPathDeg;
    float    attackAngleDeg;
    uint32_t nSnapshots;
    uint32_t nFrames;
    float    componentStdDeg;
    uint32_t trackerQuality;
    /* Mirrors LCMFResult.single_channel (lcmf.py:84). The host's candidate
     * ranking (_recovery_result_rank, iwr6843/runtime.py:103-118) sorts on
     * this directly -- a single-channel estimate must clear a larger OPS
     * speed-agreement margin before it is preferred over a corroborated one.
     * Without it the on-chip result cannot reproduce that ranking, so it is
     * not optional plumbing: 0 = dual/multi-channel, 1 = single-channel. */
    uint32_t singleChannel;
} L3SolveResult;

#endif /* L3_SOLVE_IPC_H */
