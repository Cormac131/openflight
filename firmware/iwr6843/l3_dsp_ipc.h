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

/* The DSS's boot status, written by the DSS into HS-RAM (32 KB, mapped by
 * both cores: MSS 0x52080000, DSS 0x21080000) at this offset and read by the
 * MSS when the DSS does not answer: which boot stage it reached, the error
 * if a stage failed, a heartbeat while it waits for requests, and how many
 * it has served. */
#define L3_DSP_STATUS_MAGIC         0x4C445353U /* "LDSS" */
#define L3_DSP_STATUS_HSRAM_OFFSET  0x7F00U

#define L3_DSP_STAGE_MAIN     1U  /* main() entered */
#define L3_DSP_STAGE_SOC      2U  /* SOC_init returned */
#define L3_DSP_STAGE_TASK     3U  /* BIOS started the link task */
#define L3_DSP_STAGE_MAILBOX  4U  /* Mailbox_init returned */
#define L3_DSP_STAGE_LINK     5U  /* Mailbox_open returned: serving */
#define L3_DSP_STAGE_RESET    0x10U /* xdc Reset hook: before cinit and BIOS */
#define L3_DSP_STAGE_FIRST    0x11U /* Startup.firstFxns: before the module startups */
#define L3_DSP_STAGE_LAST     0x12U /* Startup.lastFxns: after them, just before main */
#define L3_DSP_STAGE_EXCEPTION 0x13U /* the BIOS exception hook: see excPc, excFlags */
#define L3_DSP_STAGE_FAILED   0x80U /* or'd in: the stage failed, see errCode */

/* The DSS mirrors its stage into DSSREG DSSGPREG0 (MSS 0x50000400, DSS
 * 0x02000400), tagged so a reset value is not read as a stage: a second
 * channel for when HS-RAM shows nothing. */
#define L3_DSP_GPREG_TAG      0xD5500000U
#define L3_DSP_GPREG_TAG_MASK 0xFFFFFF00U

/* The DSS as the MSS sees it, without the DSS's help. */
typedef struct {
    uint32_t gpreg;   /* DSSGPREG0 */
    uint32_t halt;    /* GEMPWRSMCFG4 PWRSMLRSTHALT: 1 = held in local reset */
    uint32_t power;   /* GEMPWRSMCFG3 bits 19:18: 3 = powered up */
    uint32_t stc;     /* ROM self-test (STC) ran on the DSS */
    uint32_t esm[4];  /* ESMSR1..3 and ESMSR4: error flags, DSS errors included */
    uint32_t hsramOk; /* an MSS write to HS-RAM read back */
} l3_dsp_hw_t;

typedef struct {
    uint32_t magic;
    uint32_t stage;
    int32_t  errCode;
    uint32_t heartbeat; /* read timeouts while serving */
    uint32_t served;    /* requests answered */
    uint32_t excPc;     /* an exception's NRP, the interrupted program counter */
    uint32_t excFlags;  /* an exception's EFR; 0 when none was taken */
} l3_dsp_status_t;

uint32_t l3_dsp_request_size(void);
uint32_t l3_dsp_reply_size(void);
/* Bytes of one IQ16 frame: loops x ntx x nrx x binCount complex samples. */
uint32_t l3_dsp_frame_bytes(uint32_t ntx, uint32_t nrx, uint32_t binCount, uint32_t loops);
/* L3_DSP_OK, or why the request must not run against an L3 of l3Bytes. */
uint32_t l3_dsp_request_check(const l3_dsp_request_t *request, uint32_t l3Bytes);
uint32_t l3_dsp_status_size(void);
uint32_t l3_dsp_hw_size(void);
/* "dsp hw gpreg_stage=<name>[!FAILED] halt=H power=P stc=S
 * esm=XXXXXXXX,XXXXXXXX,XXXXXXXX,XXXXXXXX hsram=ok|BAD"; an untagged
 * register prints as none(XXXXXXXX). Returns the characters written. */
int32_t l3_dsp_hw_format(const l3_dsp_hw_t *hw, char *out, uint32_t cap);
/* "dsp status stage=<name>[ FAILED] err=E beats=B served=S", then
 * " exc_pc=XXXXXXXX exc_efr=XXXXXXXX" after an exception, or, without the
 * magic (NULL too), "dsp status stage=never_booted magic=XXXXXXXX".
 * Returns the characters written, as snprintf. */
int32_t l3_dsp_status_format(const l3_dsp_status_t *status, char *out, uint32_t cap);
/* Answer a request against the L3 arena at l3Base (this core's address for
 * it): check it, then for PROBE score its bins. The caller times the call
 * and fills reply->cycles. l3Base may be NULL for a PING. */
void l3_dsp_probe_run(const l3_dsp_request_t *request, const uint8_t *l3Base, uint32_t l3Bytes,
                      l3_dsp_reply_t *reply);

#endif /* L3_DSP_IPC_H */
