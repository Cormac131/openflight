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

/*
 * The symbol above is a RUNTIME register setting only -- it tells the cache
 * controller to carve 32 KB off the top of L2 at boot. It does nothing to
 * the linker's own view of memory: the platform file's L2SRAM_UMAP0 region
 * (below) still spans the full 256 KB as if all of it were SRAM, because
 * ti.platforms.c6x's MEMORY directive cannot be edited by an app-level cmd
 * file (TI linker errors #10263/#10264, "memory range has already been
 * specified" / "overlaps existing memory range", if you try -- confirmed
 * by testing a redefinition attempt here). Left alone, that gap is silent:
 * nothing stops .solveScratch (or any other SRAM section) from growing into
 * the address range the cache controller now owns, corrupting solve
 * intermediates with no build-time signal.
 *
 * .cacheReserve below is what actually enforces the boundary. It is a
 * real, zero-content output section pinned to the exact 32 KB the cache
 * controller claims -- 0x00818000-0x00820000, the top of L2SRAM_UMAP0,
 * since C674x L2 cache is carved from the HIGH end of L2. Because it is a
 * genuine section (not a MEMORY redefinition), the linker's allocator
 * treats that range as occupied for the rest of this link: growing
 * .solveScratch (or anything else placed in L2SRAM_UMAP0/UMAP1) until it
 * collides with .cacheReserve now fails the build with linker error #10099
 * ("program will not fit into available memory"), instead of silently
 * placing data where the cache lives. Verified by temporarily oversizing
 * .solveScratch past the remaining budget and observing exactly that error,
 * then reverting to confirm a clean, byte-identical-to-baseline rebuild.
 * See tests/test_iwr6843_dss_build.py::
 * test_l2_cache_reservation_is_enforced_by_the_linker_not_just_the_symbol.
 */
SECTIONS
{
    .cacheReserve : { . += 0x00008000; } > 0x00818000
    systemHeap    : {} > L2SRAM_UMAP0 | L2SRAM_UMAP1
    .solveScratch : {} > L2SRAM_UMAP0 | L2SRAM_UMAP1
}
/*----------------------------------------------------------------------------*/
