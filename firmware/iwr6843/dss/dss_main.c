/* DSS (C674x) image: the detect link, and the on-chip solve stages.
 *
 * The detect task is moving here from the MSS. The DSS answers the MSS over
 * the mailbox (l3_dsp_ipc.h): PING, PROBE, which scores bins of a frame in
 * the shared L3 ring with the same code the MSS runs (l3_bin_score.c), and
 * SCORE, the live detector's scan-plan spans of a frame, whose observations
 * go to the result block in HS-RAM. The DSS reads the ring in place from L3
 * and caches it in L2; it owns no resident L3 buffer.
 */
#include <c6x.h>
#include <string.h>
#include <stdint.h>
#include <xdc/std.h>
#include <ti/sysbios/BIOS.h>
#include <ti/sysbios/knl/Task.h>
#include <ti/sysbios/family/c64p/Cache.h>
#include <ti/common/sys_common.h>
#include <ti/drivers/soc/soc.h>
#include <ti/drivers/esm/esm.h>
#include <ti/drivers/mailbox/mailbox.h>

#include "../solve/solve_ipc.h"
#include "../l3_dsp_ipc.h"

/* The MSS's L3 arena, as this core addresses it. */
#define DSS_L3_BYTES (MMWAVE_L3RAM_NUM_BANK * MMWAVE_SHMEM_BANK_SIZE)

/* dss_solveTask's stack. Given explicitly via taskParams.stack/stackSize
 * below (a static array here) rather than left NULL, which would make
 * Task_create() satisfy the request by allocating stackSize bytes from
 * Task.defaultStackHeap -- Memory.defaultHeapInstance, i.e. heap0, the same
 * 32 KiB "systemHeap" dss.cfg carves out for everything else the runtime
 * allocates from (mailbox buffers included). A 32 KiB stack allocation
 * would consume that entire heap for this one task, starving every other
 * caller and defeating the point of sizing the stack generously. A static
 * array instead lands in its own .bss symbol -- visible by name in the
 * link map, sized by the compiler/linker rather than by a runtime
 * allocator, and independent of whatever else uses heap0. See the size
 * comment at its Task_create() call below. */
#define DSS_SOLVE_TASK_STACK_SIZE (32U * 1024U)

#pragma DATA_ALIGN(dss_solveTaskStack, 8)
static uint8_t dss_solveTaskStack[DSS_SOLVE_TASK_STACK_SIZE];

/* How long a read waits before the heartbeat counts one (BIOS ticks, ms). */
#define DSS_LINK_BEAT_TICKS 100U

/* The boot status in HS-RAM (l3_dsp_ipc.h), which the MSS reads when this
 * core does not answer. */
static volatile l3_dsp_status_t *const gDssStatus =
    (volatile l3_dsp_status_t *)(SOC_XWR68XX_DSS_HSRAM_BASE_ADDRESS + L3_DSP_STATUS_HSRAM_OFFSET);

/* SCORE's result block in HS-RAM (l3_dsp_ipc.h), which the MSS reads once
 * the mailbox reply says it is ready. */
static l3_dsp_result_t *const gDssResult =
    (l3_dsp_result_t *)(SOC_XWR68XX_DSS_HSRAM_BASE_ADDRESS + L3_DSP_RESULT_HSRAM_OFFSET);

/* DSSREG DSSGPREG0: the stage again, on a channel that is not HS-RAM. */
static volatile uint32_t *const gDssGpreg0 =
    (volatile uint32_t *)SOC_XWR68XX_DSS_DSSREG_BASE_ADDRESS;

/* Record a boot stage (or'd with L3_DSP_STAGE_FAILED for a failure) and
 * write it back out of this core's cache, so the MSS reads it. */
static void dss_status(uint32_t stage, int32_t errCode)
{
    *gDssGpreg0 = L3_DSP_GPREG_TAG | stage;
    gDssStatus->stage = stage;
    gDssStatus->errCode = errCode;
    gDssStatus->magic = L3_DSP_STATUS_MAGIC;
    Cache_wb((Ptr)gDssStatus, sizeof(l3_dsp_status_t), Cache_Type_ALLD, TRUE);
}

/* xdc Reset function (dss.cfg): runs before cinit and BIOS startup, so no
 * globals beyond the constant addresses and no Cache calls. Caching is not
 * yet set up, so the writes reach memory. */
void dss_resetHook(void);
void dss_resetHook(void)
{
    volatile uint32_t *gpreg = (volatile uint32_t *)SOC_XWR68XX_DSS_DSSREG_BASE_ADDRESS;
    volatile l3_dsp_status_t *status = (volatile l3_dsp_status_t *)(
        SOC_XWR68XX_DSS_HSRAM_BASE_ADDRESS + L3_DSP_STATUS_HSRAM_OFFSET);

    *gpreg = L3_DSP_GPREG_TAG | L3_DSP_STAGE_RESET;
    status->stage = L3_DSP_STAGE_RESET;
    status->errCode = 0;
    status->heartbeat = 0U;
    status->served = 0U;
    status->magic = L3_DSP_STATUS_MAGIC;
}

static void dss_statusCount(volatile uint32_t *counter)
{
    (*counter)++;
    Cache_wb((Ptr)gDssStatus, sizeof(l3_dsp_status_t), Cache_Type_ALLD, TRUE);
}

/* Answer one request from the MSS. A PROBE's or SCORE's frame was written
 * by the EDMA behind this core's L2 cache, so the cache over it is
 * invalidated before a byte is read -- the whole frame, and timed apart
 * from the scoring (TSCL cycles) so what the invalidate costs is measured
 * before anything narrower is tried. A SCORE's result is written back out
 * of the cache before the reply goes, so the MSS reads what was written. */
static void dss_answer(const l3_dsp_request_t *request, l3_dsp_reply_t *reply)
{
    const uint8_t *l3 = (const uint8_t *)SOC_XWR68XX_DSS_L3RAM_BASE_ADDRESS;
    uint8_t reads = (uint8_t)((request->cmd == L3_DSP_CMD_PROBE ||
                               request->cmd == L3_DSP_CMD_SCORE) &&
                              l3_dsp_request_check(request, DSS_L3_BYTES) == L3_DSP_OK);
    uint32_t start;
    uint32_t invCycles = 0U;

    start = TSCL;
    if (reads) {
        Cache_inv((Ptr)&l3[request->frameOffset],
                  l3_dsp_frame_bytes(request->ntx, L3_DSP_N_RX, request->binCount,
                                     request->loops),
                  Cache_Type_ALLD, TRUE);
        invCycles = TSCL - start;
    }
    start = TSCL;
    l3_dsp_serve(request, l3, DSS_L3_BYTES, reply, gDssResult);
    reply->cycles = TSCL - start;
    if (request->cmd == L3_DSP_CMD_SCORE) {
        gDssResult->invCycles = invCycles;
        gDssResult->scoreCycles = reply->cycles;
        Cache_wb((Ptr)gDssResult, sizeof(*gDssResult), Cache_Type_ALLD, TRUE);
    }
}

/* The detect link: read a request, answer it, release the mailbox for the
 * next. A message of the wrong size is answered with ERR_MAGIC so the MSS
 * does not wait out its timeout. The read is bounded so the heartbeat shows
 * this task alive while nothing arrives. */
static void dss_solveTask(UArg arg0, UArg arg1)
{
    Mailbox_Config cfg;
    Mbox_Handle link;
    int32_t errCode = 0;

    (void)arg0;
    (void)arg1;
    TSCL = 0U; /* any write starts the free-running cycle counter */
    dss_status(L3_DSP_STAGE_TASK, 0);
    errCode = Mailbox_init(MAILBOX_TYPE_DSS);
    if (errCode < 0 || Mailbox_Config_init(&cfg) < 0) {
        dss_status(L3_DSP_STAGE_MAILBOX | L3_DSP_STAGE_FAILED, errCode);
        return;
    }
    dss_status(L3_DSP_STAGE_MAILBOX, 0);
    cfg.readMode = MAILBOX_MODE_BLOCKING;
    cfg.readTimeout = DSS_LINK_BEAT_TICKS;
    cfg.writeMode = MAILBOX_MODE_BLOCKING;
    cfg.writeTimeout = 100U;
    cfg.chType = MAILBOX_CHTYPE_MULTI;
    cfg.chId = MAILBOX_CH_ID_0;
    link = Mailbox_open(MAILBOX_TYPE_MSS, &cfg, &errCode);
    if (link == NULL || errCode != 0) {
        dss_status(L3_DSP_STAGE_LINK | L3_DSP_STAGE_FAILED, errCode);
        return;
    }
    dss_status(L3_DSP_STAGE_LINK, 0);
    while (1) {
        l3_dsp_request_t request;
        l3_dsp_reply_t reply;
        int32_t got;

        memset(&request, 0, sizeof(request));
        got = Mailbox_read(link, (uint8_t *)&request, sizeof(request));
        if (got <= 0) {
            dss_statusCount(&gDssStatus->heartbeat); /* timed out: nothing to release */
            continue;
        }
        (void)Mailbox_readFlush(link);
        if (got != (int32_t)sizeof(request)) {
            request.magic = 0U;
        }
        dss_answer(&request, &reply);
        (void)Mailbox_write(link, (const uint8_t *)&reply, sizeof(reply));
        dss_statusCount(&gDssStatus->served);
    }
}

int main(void)
{
    Task_Params taskParams;
    SOC_Handle socHandle;
    int32_t errCode;
    SOC_Cfg socCfg;

    gDssStatus->heartbeat = 0U;
    gDssStatus->served = 0U;
    dss_status(L3_DSP_STAGE_MAIN, 0);
    memset((void *)&socCfg, 0, sizeof(SOC_Cfg));
    /* The MSS owns the system clock and the BSS. SOC_SysClock_INIT here
     * would ungate and unhalt the BSS again and spin on the APLL calibration
     * flag; TI's mmw demo DSS bypasses it the same way. */
    socCfg.clockCfg = SOC_SysClock_BYPASS_INIT;
    socHandle = SOC_init(&socCfg, &errCode);
    if (socHandle == NULL) {
        dss_status(L3_DSP_STAGE_SOC | L3_DSP_STAGE_FAILED, errCode);
        return -1;
    }
    dss_status(L3_DSP_STAGE_SOC, 0);

    Task_Params_init(&taskParams);
    taskParams.priority = 2;
    /* Stack sized to the largest solve stage that runs on this task, not to
     * what Milestone 1 (Task_sleep only) needs. dss_solveTask calls straight
     * into the ported solve stages (solve_fft_apply() as of Task 4; Tasks
     * 5-8 add more), and every stage's large scratch buffers are ordinary
     * stack locals -- see solve_fft.c's solve_fft_apply()/
     * solve_fft_transform_dsplib(). Those two frames are simultaneously live
     * (solve_fft_apply calls solve_fft_transform -> solve_fft_transform_dsplib,
     * a normal nested call, not a coroutine), so their sizes add:
     *
     *   solve_fft_apply:              window[128] f64  = 1,024 B
     *                                 re[512] f64       = 4,096 B
     *                                 im[512] f64       = 4,096 B
     *                                 scalars (row, i)  ~    16 B
     *                                                    --------
     *                                                      9,232 B
     *   solve_fft_transform_dsplib:   x[2*512] f32      = 4,096 B
     *                                 y[2*512] f32      = 4,096 B
     *                                 w[2*512] f32      = 4,096 B
     *                                 scalar i           ~     8 B
     *                                                    --------
     *                                                     12,296 B
     *                                            combined 21,528 B
     *
     * Plus margin for solve_fft_gen_twiddle()/DSPF_sp_fftSPxSP() (leaf calls
     * from solve_fft_transform_dsplib, not simultaneous with each other --
     * DSPF_sp_fftSPxSP is a compiled DSPLIB kernel we cannot inspect the
     * source of, budget 512 B) and per-call-level linkage overhead across
     * the ~6-deep chain (dss_solveTask -> future mailbox dispatch ->
     * solve_fft_apply -> solve_fft_transform -> solve_fft_transform_dsplib ->
     * {gen_twiddle|DSPF_sp_fftSPxSP}, ~64 B/level, 384 B): a derived (NOT
     * silicon-measured) worst case of roughly 22.4 KiB against the old 4 KiB
     * budget.
     *
     * 32 KiB gives ~10 KiB of headroom over that derived figure -- comfortably
     * inside the 229,376 B DSS L2 ceiling the .cacheReserve linker section
     * enforces (see dss_linker.cmd); see the Task 4 report for the L2 usage
     * this leaves.
     *
     * IMPORTANT: Tasks 5-8 each add a stage that runs on this same task.
     * Before landing a stage, re-derive this sum with that stage's own
     * stack-resident buffers (per the porting recipe in the Task 4 report)
     * and raise this number if the new stage's frame, combined with
     * whatever it calls into, exceeds the current headroom. Do not just
     * bump this number without redoing the arithmetic above (and update
     * DSS_SOLVE_TASK_STACK_SIZE's definition above to match).
     *
     * Task 5 (tracking stage, solve_tracking.c): no change needed. Unlike
     * solve_fft_apply(), solve_tracking_find_ball()'s large scratch (the
     * detection/order arrays and the one-row MTI buffer, tens of KB at
     * SOLVE_TRACKING_MAX_* sizes) lives in a caller-owned
     * SolveTrackingWorkspace passed BY POINTER, not as a local -- deliberately,
     * because those buffers are far too large for any reasonable stack
     * budget (see solve_tracking.h). The function's own stack frame and its
     * call chain (-> solve_tracking_scan -> solve_tracking_loop_power/
     * solve_tracking_detect -> solve_tracking_median) are all scalars and
     * small fixed locals, on the order of 100-200 B total, not simultaneous
     * with solve_fft's own ~21.5 KiB frame pair (a different stage, run at a
     * different time on the same task). 32 KiB stays comfortably sized for
     * either stage. The workspace itself is NOT yet instantiated anywhere in
     * this DSS build (no caller invokes solve_tracking_find_ball from
     * dss_main.c -- there is no on-chip MTI-cube producer yet for it to
     * consume), so it costs zero L2 today; whoever wires the real mailbox
     * dispatch (Task 6+) must budget ~42 KB of static workspace against the
     * L2 headroom at that point -- see the Task 5 report.
     *
     * Task 6 (lcmf stage, solve_lcmf.c): no change needed, but for a
     * different reason than Task 5 -- solve_lcmf_estimate()'s own stack
     * frame is NOT small. Its named locals (gridDeg/objectiveTwo8/
     * objectiveF4 at SOLVE_LCMF_MAX_GRID=512 doubles each = 4,096 B x 3 =
     * 12,288 B; the per-model rangeM/frameIds/errors scratch at
     * SOLVE_LCMF_MAX_SELECTED=256 = 2,048+512+2,048 B; smaller per-block
     * locals) sum to a conservative (no stack-slot reuse assumed, same
     * methodology as the solve_fft derivation above) ~17.75 KiB (18,174 B).
     * The deepest simultaneously-live call chain underneath that --
     * leave_one_channel_out_error() (gram/gramInv/pseudo/coefficients/
     * prediction/leverage, ~1.8 KiB) nesting into its own
     * cmat_inverse() (~1.1 KiB) -- adds ~2.8 KiB (2,866 B; frame_objective's
     * own ~2.2 KiB frame and spatial_dictionary's ~0.8 KiB frame run at
     * DIFFERENT points in the same loop, never simultaneously with the
     * pinv chain or each other, so they are not summed here). Derived
     * total: 18,174 + 2,866 = 21,040 B, ~20.5 KiB -- a source-level worst case, NOT a
     * compiler- or silicon-measured figure (no hardware was run for this
     * task). That is LESS than solve_fft's own ~21.5 KiB frame pair the
     * current 32 KiB was already sized against, and the two stages never
     * run simultaneously (one task, one stage at a time), so no change to
     * DSS_SOLVE_TASK_STACK_SIZE is needed. Unlike Task 5's workspace,
     * solve_lcmf's SolveLcmfWorkspace (~1 MB at SOLVE_LCMF_MAX_* sizes,
     * dominated by the ~1 MB vecRe/vecIm snapshot cache) is ALSO a
     * caller-owned pointer, not a stack local -- whoever wires the real
     * mailbox dispatch must budget that separately against L2/L3, same
     * caveat as Task 5's workspace.
     */
    taskParams.stack = dss_solveTaskStack;
    taskParams.stackSize = sizeof(dss_solveTaskStack);
    Task_create(dss_solveTask, &taskParams, NULL);

    BIOS_start();
    return 0;
}
