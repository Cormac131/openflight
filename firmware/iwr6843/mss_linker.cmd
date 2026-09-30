/*----------------------------------------------------------------------------*/
/* OpenFlight Stage-1c L3 burst-dump — MSS application linker command file.   */
/*                                                                            */
/* The platform file (ti/platform/xwr68xx/r4f_linker.cmd, added on the make   */
/* link line) defines the MEMORY regions (VECTORS/PROG_RAM/DATA_RAM/L3_RAM/   */
/* HS_RAM) and places the standard sections (.text/.const/.bss/.data/.stack). */
/* Here we only add the app-specific sections:                                */
/*   systemHeap  - the SYS/BIOS system heap created in mss.cfg                 */
/*   .l3ring     - the rolling buffer (raw ADC for legacy builds, compact      */
/*                 FFT range snapshots for HWA_CHAINED_SNAPSHOT_RING builds).  */
/*   .dataScratch - the IQ16 ping/pong frame scratch used by chained-snapshot  */
/*                 builds before HWA compression into .l3ring. It lives in     */
/*                 DATA_RAM (not L3_RAM) so IQ8 capture owns the whole L3      */
/*                 arena. If these sections overflow their region, the link    */
/*                 fails loudly.                                               */
/*----------------------------------------------------------------------------*/
--retain="*(.intvecs)"

SECTIONS
{
    systemHeap   : {} > DATA_RAM
    .l3ring      : {} > L3_RAM
    .dataScratch : {} > DATA_RAM
    /* MSS-only diagnostics (timing, profile, CLI line buffers; see
     * L3_HSRAM_DIAG in l3_dump.c): DATA_RAM is full. HS-RAM's lower 29 KB
     * is unused by the MSS; its top 3 KB holds the DSS's words (SCORE result
     * 0x7400, the probe word 0x7E00, the status 0x7F00), reserved below so
     * a growing .hsramMss fails this link instead of overwriting them. */
    .hsramDss    : { . += 0x00000C00; } > 0x52087400
    .hsramMss    : {} > HS_RAM
}
/*----------------------------------------------------------------------------*/
