/* DSS (C674x) image for the on-chip solve.
 *
 * Milestone 1: boot, register with the mailbox, and answer a ping. The solve
 * stages land on top of this in later tasks. The DSS reads the capture arena
 * in place from L3 and caches it in L2; it owns no resident L3 buffer.
 */
#include <string.h>
#include <stdint.h>
#include <xdc/std.h>
#include <ti/sysbios/BIOS.h>
#include <ti/sysbios/knl/Task.h>
#include <ti/drivers/soc/soc.h>
#include <ti/drivers/esm/esm.h>
#include <ti/drivers/mailbox/mailbox.h>

#include "../solve/solve_ipc.h"

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

static void dss_solveTask(UArg arg0, UArg arg1)
{
    (void)arg0;
    (void)arg1;
    while (1) {
        /* Milestone 1: no work yet. Task 6 replaces this with the mailbox
         * listener. Yield so BIOS is demonstrably scheduling us. */
        Task_sleep(100U);
    }
}

int main(void)
{
    Task_Params taskParams;
    SOC_Handle socHandle;
    int32_t errCode;
    SOC_Cfg socCfg;

    memset((void *)&socCfg, 0, sizeof(SOC_Cfg));
    socCfg.clockCfg = SOC_SysClock_INIT;
    socHandle = SOC_init(&socCfg, &errCode);
    if (socHandle == NULL) {
        return -1;
    }

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
     */
    taskParams.stack = dss_solveTaskStack;
    taskParams.stackSize = sizeof(dss_solveTaskStack);
    Task_create(dss_solveTask, &taskParams, NULL);

    BIOS_start();
    return 0;
}
