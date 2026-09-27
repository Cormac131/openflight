/* IWR6843 adaptive capture windows.
 *
 * The capture plan's range windows (pre, impact, post, late) were fixed
 * physical stretches chosen at sensorStart. Once the ball detector has
 * locked the ball at a global bin, the useful region is known: the approach
 * ends at the ball, impact happens at the ball, the flight starts there and
 * moves away. This module turns a locked ball bin into window starts for
 * the plan's existing widths, so L3 is spent on where the shot is:
 *
 *   pre    = ball - approachBins          (the downswing's last stretch)
 *   impact = ball - marginBins            (the ball and a little short of it)
 *   post   = ball - marginBins            (the first metre of flight)
 *   late   = post + postBins / 2          (the flight continuing outward)
 *
 * each clipped so the window stays inside the FFT. l3_dump.c applies the
 * result between shots (at rearm, when the plan can be rebuilt), never
 * while a capture is running. Pure C, no hardware.
 */
#ifndef L3_ADAPTIVE_H
#define L3_ADAPTIVE_H

#include <stdint.h>

typedef struct {
    uint8_t enabled;
    uint8_t approachBins;  /* pre window starts this many bins short of the ball */
    uint8_t marginBins;    /* impact and post windows start this many bins short */
} l3_adaptive_cfg_t;

typedef struct {
    uint8_t preStart;
    uint8_t impactStart;
    uint8_t postStart;
    uint8_t lateStart;
} l3_adaptive_windows_t;

void l3_adaptive_cfg_defaults(l3_adaptive_cfg_t *cfg);
/* Window starts for a ball at ballBin (global) given the plan's widths and
 * the FFT size. Returns 1 when the configuration is enabled and the windows
 * were computed, 0 when disabled or the ball bin is outside the FFT. */
int32_t l3_adaptive_windows(const l3_adaptive_cfg_t *cfg, uint32_t ballBin, uint32_t fftBins,
                            uint32_t preBins, uint32_t impactBins, uint32_t postBins,
                            l3_adaptive_windows_t *out);
/* 1 when out differs from the current starts. */
int32_t l3_adaptive_differs(const l3_adaptive_windows_t *out, uint32_t preStart,
                            uint32_t impactStart, uint32_t postStart, uint32_t lateStart);
/* "adaptive enabled=1 approach=24 margin=4 pre=22 impact=42 post=42 late=68" */
int32_t l3_adaptive_format(const l3_adaptive_cfg_t *cfg, const l3_adaptive_windows_t *out,
                           char *text, uint32_t cap);

#endif /* L3_ADAPTIVE_H */
