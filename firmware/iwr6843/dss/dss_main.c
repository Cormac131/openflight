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
    taskParams.stackSize = 4 * 1024;
    Task_create(dss_solveTask, &taskParams, NULL);

    BIOS_start();
    return 0;
}
