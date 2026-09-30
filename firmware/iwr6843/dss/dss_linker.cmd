/*----------------------------------------------------------------------------*/
/* OpenFlight on-chip solve -- DSS application linker command file.          */
/*                                                                            */
/* The platform file (ti/platform/xwr68xx/c674x_linker.cmd, added on the      */
/* make link line via PLATFORM_C674X_LINK_CMD) defines the MEMORY regions     */
/* (L2SRAM_UMAP0/L2SRAM_UMAP1/L3SRAM/HWA_RAM/HSRAM) and already places the    */
/* standard sections -- .text, .const, .data, .bss, .stack, .cinit, .switch,  */
/* .far, .fardata, .cio, .rodata, .neardata -- into L2SRAM_UMAP0/UMAP1 (L2    */
/* SRAM). We do not need to (and per memory decision 22, must not) redeclare  */
/* those here.                                                                */
/*                                                                            */
/* This file adds only the app-specific section:                             */
/*   systemHeap    - the SYS/BIOS system heap created in dss.cfg.            */
/*   .solveScratch - solve intermediates (RANSAC candidates, per-frame       */
/*                   angle/path accumulators, etc.). Lives in L2 SRAM, same  */
/*                   as everything else in this image.                       */
/*                                                                            */
/* Memory decision 22 (on-chip solve spec): the DSS owns NO resident L3       */
/* buffer. It reads the capture arena in place from L3 through L1D (the      */
/* platform's caches, below), not a linker-placed L3 section. Accordingly    */
/* this file references L2SRAM_UMAP0/UMAP1 only -- it must never gain an     */
/* "> L3SRAM" placement.                                                     */
/*----------------------------------------------------------------------------*/
--retain="*(.intvecs)"

/*
 * Caches stay at the platform's (c674x_linker.cmd): L1P and L1D 16 KB of
 * cache each, L2 all SRAM, as TI's mmw demo DSS. An earlier override,
 * ti_sysbios_family_c64p_Cache_l2Size = 1 (32 KB of L2 as cache over L3,
 * with a .cacheReserve section holding the linker off that range), left
 * the DSS dead in its BIOS module startups on the board (2026-09-30:
 * Startup.firstFxns reached, lastFxns never). L3 is read through L1D.
 */
SECTIONS
{
    systemHeap    : {} > L2SRAM_UMAP0 | L2SRAM_UMAP1
    .solveScratch : {} > L2SRAM_UMAP0 | L2SRAM_UMAP1
}
/*----------------------------------------------------------------------------*/
