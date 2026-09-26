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
/* buffer. It reads the capture arena in place from L3 through the L2 cache  */
/* configured below, not through a linker-placed L3 section. Accordingly     */
/* this file references L2SRAM_UMAP0/UMAP1 only -- it must never gain an     */
/* "> L3SRAM" placement.                                                     */
/*----------------------------------------------------------------------------*/
--retain="*(.intvecs)"

/*
 * L2 SRAM/cache split: 32 KB of L2 as cache over L3, the rest as SRAM.
 * ti.sysbios.family.c64p.Cache on the ti.platforms.c6x platform (used here)
 * rejects Cache.initSize from a cfg script and requires this split as linker
 * symbols instead (see dss.cfg for the full explanation). This overrides the
 * l2Size=0 (all-SRAM) default the platform's c674x_linker.cmd would
 * otherwise leave in place, since this file is included after it on the
 * link line (see ../dss/makefile). L1P/L1D stay at the platform default
 * (16 KB cache / 16 KB SRAM each) -- only L2 is a Task-2 decision.
 */
ti_sysbios_family_c64p_Cache_l2Size = 1; /* Cache.L2Size_32K */

SECTIONS
{
    systemHeap    : {} > L2SRAM_UMAP0 | L2SRAM_UMAP1
    .solveScratch : {} > L2SRAM_UMAP0 | L2SRAM_UMAP1
}
/*----------------------------------------------------------------------------*/
