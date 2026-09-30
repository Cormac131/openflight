/* IWR6843 MSS <-> DSS detect link: the mailbox messages.
 *
 * The detect task is moving from the R4F (MSS) to the C674x (DSS). Phase 0
 * proves the link on the board: PING, and PROBE, which scores bins of a
 * frame in the shared L3 ring with l3_bin_score on whichever core runs
 * l3_dsp_probe_run, so the MSS can run the same probe and compare.
 *
 * SCORE is the live detector's: the scan plan's spans (l3_scan.h) of one
 * ring frame, scored with the same code, the observations left in a result
 * block in HS-RAM (l3_dsp_result_t) and the mailbox reply only the signal
 * that it is ready. The MSS keeps the observation -> tracker -> trigger
 * path; l3_detect_core.h decides per frame which core scores.
 *
 * The cores see L3 at different addresses (MSS 0x51000000, DSS 0x20000000),
 * so a frame travels as its byte offset into L3; l3_dsp_request_check
 * refuses anything that would read outside it. Fixed-width fields only, so
 * both compilers lay a message out the same way.
 */
#ifndef L3_DSP_IPC_H
#define L3_DSP_IPC_H

#include <stdint.h>

#include "l3_observation.h"
#include "l3_scan.h"

#define L3_DSP_MAGIC 0x4C445331U /* "LDS1" */

#define L3_DSP_CMD_PING  1U
#define L3_DSP_CMD_PROBE 2U
#define L3_DSP_CMD_SCORE 3U

#define L3_DSP_OK           0U
#define L3_DSP_ERR_MAGIC    1U
#define L3_DSP_ERR_CMD      2U
#define L3_DSP_ERR_GEOMETRY 3U /* ntx, loops, or bins outside the frame */
#define L3_DSP_ERR_RANGE    4U /* the frame is misaligned or not inside L3 */
#define L3_DSP_ERR_SPANS    5U /* SCORE: no spans, too many, or one outside the frame */
#define L3_DSP_ERR_STALE    6U /* MSS side: the result answers another request */

/* Every RX is scored (the board has four). */
#define L3_DSP_N_RX 4U

/* SCORE: at most this many spans (the pre-impact plan's region, club, leave
 * and map chunk), and only a frame's first L3_DSP_MAX_BINS local bins, the
 * detector's own limit (L3_TRIG_MAX_BINS). */
#define L3_DSP_MAX_SPANS 4U
#define L3_DSP_MAX_BINS  64U
#define L3_DSP_BITMAP_WORDS (L3_DSP_MAX_BINS / 32U)

typedef struct {
    uint32_t magic;
    uint32_t cmd;
    uint32_t seq;
    uint32_t frameOffset; /* bytes from the start of L3 to the frame */
    uint32_t binCount;    /* bins stored per (loop, tx, rx) */
    uint32_t firstBin;    /* PROBE: local bin of the first scored */
    uint32_t nBins;       /* PROBE */
    uint32_t ntx;
    uint32_t loops;
    uint32_t epoch;       /* SCORE: the detect queue's epoch, echoed in the result */
    uint32_t nSpans;      /* SCORE: spans in spanFirst/spanCount */
    uint32_t spanFirst[L3_DSP_MAX_SPANS]; /* SCORE: LOCAL first bin */
    uint32_t spanCount[L3_DSP_MAX_SPANS];
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

/* SCORE's answer, in HS-RAM (both cores map it) below the boot status. The
 * DSS writes it and writes it back out of its cache before the mailbox
 * reply; the MSS accepts it only for the request it sent (magic, seq and
 * epoch: l3_dsp_result_check), so a late answer to a request it gave up on
 * is never read as this one's. obs[] is indexed by LOCAL bin, as the
 * detector's own observation array is; scored[] marks the bins filled. */
#define L3_DSP_RESULT_MAGIC         0x4C445352U /* "LDSR" */
#define L3_DSP_RESULT_HSRAM_OFFSET  0x7400U     /* 128-byte aligned: its own cache lines */

typedef struct {
    uint32_t magic;
    uint32_t seq;
    uint32_t epoch;
    uint32_t status;       /* L3_DSP_OK or L3_DSP_ERR_* */
    uint32_t count;        /* bins scored: the bits set in scored[] */
    uint32_t invCycles;    /* DSS cycles invalidating the frame's cache */
    uint32_t scoreCycles;  /* DSS cycles scoring */
    uint32_t reserved;
    uint32_t scored[L3_DSP_BITMAP_WORDS];
    l3_bin_obs_t obs[L3_DSP_MAX_BINS];
} l3_dsp_result_t;

/* Which field of a bin two cores disagreed on (l3_dsp_result_compare). */
#define L3_DSP_FIELD_ENERGY 0U
#define L3_DSP_FIELD_PEAK   1U
#define L3_DSP_FIELD_LOOP0  2U
#define L3_DSP_FIELD_R1RE   3U
#define L3_DSP_FIELD_R1IM   4U
#define L3_DSP_FIELD_SET    5U /* one core scored the bin, the other did not */

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

/* --- SCORE ------------------------------------------------------------------ */

uint32_t l3_dsp_result_size(void);

/* One bin's observation, however a core computes it: 0, or nonzero when the
 * bin could not be scored (it is then left unmarked). */
typedef int32_t (*l3_bin_scorer_fn)(void *ctx, uint32_t localBin, l3_bin_obs_t *out);

/* The shared scorer for an IQ16 frame: l3_bin_score_iq16 over every RX. */
typedef struct {
    const int16_t *frame;
    uint32_t binCount;
    uint32_t ntx;
    uint32_t loops;
} l3_dsp_iq16_ctx_t;
int32_t l3_dsp_iq16_scorer(void *ctx, uint32_t localBin, l3_bin_obs_t *out);

/* The scan plan's GLOBAL spans as a frame's LOCAL ones: each clipped to the
 * frame's first min(binCount, L3_DSP_MAX_BINS) bins (binStart onward), the
 * empty ones dropped, order kept. Returns how many were written to local;
 * 0 for more than L3_DSP_MAX_SPANS spans (callers pass fixed arrays of at
 * most that many). */
uint32_t l3_dsp_spans_localize(uint32_t binStart, uint32_t binCount, const l3_span_t *global,
                               uint32_t n, l3_span_t *local);

/* Score every bin of the LOCAL spans with scorer into obs[local], once each:
 * a bin already marked in scored[] (L3_DSP_BITMAP_WORDS words) is skipped,
 * so overlapping spans cost nothing twice; bins at or past
 * min(binCount, L3_DSP_MAX_BINS) are skipped. Returns the bins newly
 * scored. What the MSS runs for its own scoring and the DSS for SCORE, so
 * the two agree on which bins a frame scores. */
uint32_t l3_dsp_spans_score(const l3_span_t *spans, uint32_t nSpans, uint32_t binCount,
                            l3_bin_scorer_fn scorer, void *ctx, l3_bin_obs_t *obs,
                            uint32_t *scored);

/* Answer any request against the L3 arena at l3Base (this core's address for
 * it). PING and PROBE as l3_dsp_probe_run (result untouched). SCORE: check
 * it, score its spans with the shared IQ16 scorer into result (magic, seq,
 * epoch, status, count, scored, obs; the cycle fields are the caller's),
 * and set reply->status and reply->nBins to match. A refused SCORE leaves
 * result with its status, count 0 and nothing marked. result may be NULL
 * for PING and PROBE; a SCORE without one is refused with ERR_RANGE. */
void l3_dsp_serve(const l3_dsp_request_t *request, const uint8_t *l3Base, uint32_t l3Bytes,
                  l3_dsp_reply_t *reply, l3_dsp_result_t *result);

/* L3_DSP_OK when result answers the request with this seq and epoch, says
 * OK, and its count is the bits it marks; L3_DSP_ERR_STALE when magic, seq
 * or epoch differ (an answer to another request) or the count disagrees;
 * else the result's own status. */
uint32_t l3_dsp_result_check(const l3_dsp_result_t *result, uint32_t seq, uint32_t epoch);

/* A checked result into the detector's arrays: each bin result marks and
 * scored[] does not is copied to obs[] and marked. Returns how many. */
uint32_t l3_dsp_result_merge(const l3_dsp_result_t *result, l3_bin_obs_t *obs,
                             uint32_t *scored);

/* Bit for bit: the bins result marks against the bins mssScored marks and
 * their mss[] values. Returns 1 when identical; 0 with the first local bin
 * that differs and which field (L3_DSP_FIELD_*; SET when only one core
 * scored it). bin and field may be NULL. */
int32_t l3_dsp_result_compare(const l3_dsp_result_t *result, const l3_bin_obs_t *mss,
                              const uint32_t *mssScored, uint32_t *bin, uint32_t *field);

#endif /* L3_DSP_IPC_H */
