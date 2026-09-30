/* IWR6843 MSS <-> DSS detect link: the mailbox messages.
 *
 * The detect task is moving from the R4F (MSS) to the C674x (DSS). Phase 0
 * proves the link on the board: PING, and PROBE, which scores bins of a
 * frame in the shared L3 ring with l3_bin_score on whichever core runs
 * l3_dsp_probe_run, so the MSS can run the same probe and compare.
 *
 * The cores see L3 at different addresses (MSS 0x51000000, DSS 0x20000000),
 * so a frame travels as its byte offset into L3; l3_dsp_request_check
 * refuses anything that would read outside it. Fixed-width fields only, so
 * both compilers lay a message out the same way.
 */
#ifndef L3_DSP_IPC_H
#define L3_DSP_IPC_H

#include <stdint.h>

#define L3_DSP_MAGIC 0x4C445331U /* "LDS1" */

#define L3_DSP_CMD_PING  1U
#define L3_DSP_CMD_PROBE 2U

#define L3_DSP_OK           0U
#define L3_DSP_ERR_MAGIC    1U
#define L3_DSP_ERR_CMD      2U
#define L3_DSP_ERR_GEOMETRY 3U /* ntx, loops, or bins outside the frame */
#define L3_DSP_ERR_RANGE    4U /* the frame is misaligned or not inside L3 */

/* Every RX is scored (the board has four). */
#define L3_DSP_N_RX 4U

typedef struct {
    uint32_t magic;
    uint32_t cmd;
    uint32_t seq;
    uint32_t frameOffset; /* bytes from the start of L3 to the frame */
    uint32_t binCount;    /* bins stored per (loop, tx, rx) */
    uint32_t firstBin;    /* local bin of the first scored */
    uint32_t nBins;
    uint32_t ntx;
    uint32_t loops;
} l3_dsp_request_t;

typedef struct {
    uint32_t magic;
    uint32_t cmd;
    uint32_t seq;
    uint32_t status;   /* L3_DSP_OK or L3_DSP_ERR_* */
    uint32_t nBins;    /* bins scored */
    uint32_t cycles;   /* the scoring's CPU cycles on the core that ran it */
    float    energySum;
    float    r1ReSum;
    float    r1ImSum;
} l3_dsp_reply_t;

uint32_t l3_dsp_request_size(void);
uint32_t l3_dsp_reply_size(void);
/* Bytes of one IQ16 frame: loops x ntx x nrx x binCount complex samples. */
uint32_t l3_dsp_frame_bytes(uint32_t ntx, uint32_t nrx, uint32_t binCount, uint32_t loops);
/* L3_DSP_OK, or why the request must not run against an L3 of l3Bytes. */
uint32_t l3_dsp_request_check(const l3_dsp_request_t *request, uint32_t l3Bytes);
/* Answer a request against the L3 arena at l3Base (this core's address for
 * it): check it, then for PROBE score its bins. The caller times the call
 * and fills reply->cycles. l3Base may be NULL for a PING. */
void l3_dsp_probe_run(const l3_dsp_request_t *request, const uint8_t *l3Base, uint32_t l3Bytes,
                      l3_dsp_reply_t *reply);

#endif /* L3_DSP_IPC_H */
