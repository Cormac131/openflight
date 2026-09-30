/* IWR6843 scan plan: which range bins a frame scores.
 *
 * The board scores a bin (l3_verticalResidual) in ~73 us (triggerLog perf,
 * 2026-09-30) and has 3 ms a frame. With the tee band on it scored the
 * trigger region and then the whole window, ~5.1 ms, and the detect task,
 * which outranks the CLI and the trigger notices, starved them the moment the
 * trigger was armed: the board answered nothing and fired nothing. The plan
 * scores only what is read:
 *
 *   before impact  the club's approach, clubBins short of the band's near
 *                  edge (the edge bin too, as the last peak's neighbour); the
 *                  ball-leave fallback's stretch, leaveBins beyond its far
 *                  edge; the trigger region clipped to short of the band;
 *                  and on idle frames a rotating mapChunkBins of the band's
 *                  interior for its noise map, which nothing else scores
 *   after impact   postBins following the ball track, postBehindBins short
 *                  of its predicted bin, from just beyond the band until the
 *                  ball is tracked (the band's bins are dropped anyway); and
 *                  postClubBins following the club track
 *                  a bin short of its prediction, just beyond the band until
 *                  it takes the follow-through (the ball alone lost the club
 *                  after impact: usable club_out 6 -> 0 and consistent impact
 *                  fits 7 -> 2 on the labelled swings; 12 + 4 kept 5 and 7)
 *
 * 27 bins a swing frame, 29 an idle one, 16 after impact, against the 69
 * (the region, then all 53) the band path scored. Every span is in GLOBAL
 * bins and clipped to the frame's window. Pure C, no hardware.
 */
#ifndef L3_SCAN_H
#define L3_SCAN_H

#include <stdint.h>

#include "l3_band.h"

typedef struct {
    uint32_t first;   /* global bin */
    uint32_t count;   /* 0: nothing */
} l3_span_t;

typedef struct {
    uint32_t clubBins;        /* approach short of the band */
    uint32_t leaveBins;       /* beyond the band, for the ball-leave fallback */
    uint32_t postBins;        /* after impact, following the ball */
    uint32_t postBehindBins;  /* the ball's window starts this far short of it */
    uint32_t postClubBins;    /* after impact, following the club */
    uint32_t mapChunkBins;    /* band interior refreshed per idle frame */
} l3_scan_cfg_t;

void l3_scan_cfg_defaults(l3_scan_cfg_t *cfg);
/* Before impact. With a valid band: region = the trigger region clipped to
 * short of the band (bins up to floor(loBin)); club = clubBins short of
 * floor(loBin) through it; leave = leaveBins from floor(hiBin) + 1. Without
 * one: club = the trigger region, leave empty. */
void l3_scan_pre(const l3_scan_cfg_t *cfg, uint32_t windowFirst, uint32_t windowCount,
                 uint32_t regionFirst, uint32_t regionCount, const l3_band_t *band,
                 l3_span_t *region, l3_span_t *club, l3_span_t *leave);
/* An idle frame's chunk of the band's interior (floor(loBin) + 1 through
 * floor(hiBin)), from *cursor, which advances and wraps. Empty without a band. */
void l3_scan_map_chunk(const l3_scan_cfg_t *cfg, uint32_t windowFirst, uint32_t windowCount,
                       const l3_band_t *band, uint32_t *cursor, l3_span_t *out);
/* After impact. ball: postBins from floor(band->hiBin) + 1, or with the
 * ball tracked from floor(ballPredicted) - postBehindBins but never into the
 * band; club: postClubBins from floor(band->hiBin) + 1, or with the club
 * tracked from floor(clubPredicted) - 1. Each kept whole inside the window. */
void l3_scan_post(const l3_scan_cfg_t *cfg, uint32_t windowFirst, uint32_t windowCount,
                  const l3_band_t *band, uint8_t ballTracking, float ballPredicted,
                  uint8_t clubTracking, float clubPredicted, l3_span_t *ball, l3_span_t *club);
/* a and b as disjoint spans, first bin ascending: one when they overlap or
 * touch, so no bin is extracted twice. Returns how many (0-2) are in out. */
uint32_t l3_scan_merge(l3_span_t a, l3_span_t b, l3_span_t *out);
/* Distinct bins the spans cover together. */
uint32_t l3_scan_count(const l3_span_t *spans, uint32_t n);

#endif /* L3_SCAN_H */
