/* IWR6843 per-channel loops over a detect frame.
 *
 * Every (tx, rx) channel of one range bin, over the loops: the burst-MTI
 * residual the trigger scores, the static power the ball detector reads,
 * and the two angle snapshots (a moving target's, Doppler unwound; a static
 * target's, raw). One module the board calls and the host tests against the
 * replay's numpy mirrors; each sample is read from the frame once (on the
 * R4F the frame is uncached L3). Pure C, no hardware.
 *
 * Frame layout, as the ring stores it: [loop][tx][rx][bin][Im, Re], cb
 * bytes a component (2: IQ16; 1: IQ8, times scale). The vertical pair is
 * every TX but the azimuth element (TX1 of a three-TX loop).
 */
#ifndef L3_CHANNELS_H
#define L3_CHANNELS_H

#include <stdint.h>

#include "l3_angle.h"
#include "l3_observation.h"

#define L3_CHANNELS_MAX_LOOPS 16U
#define L3_CHANNELS_MAX_TX    3U
#define L3_CHANNELS_MAX_RX    4U

typedef struct {
    const uint8_t *base;   /* the frame's first byte */
    uint32_t binCount;     /* bins stored per (loop, tx, rx) */
    uint32_t cb;           /* bytes a component: 2 (IQ16) or 1 (IQ8) */
    float    scale;        /* physical amplitude per stored unit */
    uint32_t ntx;
    uint32_t nrx;
    uint32_t loops;
    float    loopPeriodS;  /* a loop's duration: the snapshots' chirp period is this / ntx */
} l3_channel_frame_t;

/* 1 when the geometry is one these loops can read: base set, loops
 * 1..L3_CHANNELS_MAX_LOOPS, ntx 1..3, nrx 1..4, cb 1 or 2, localBin inside. */
int32_t l3_channels_valid(const l3_channel_frame_t *frame, uint32_t localBin);

/* The burst-MTI residual of one bin over the vertical pair and every RX:
 * obs (energy, peak loop, loop 0, lag-1 autocorrelation) and perLoop (each
 * loop's power; NULL for none). IQ16 is the shared exact integer scoring
 * (l3_bin_score_iq16); IQ8 the float path. An invalid geometry zeroes obs
 * and leaves perLoop untouched. obs may be NULL. */
void l3_channels_residual(const l3_channel_frame_t *frame, uint32_t localBin, float *perLoop,
                          l3_bin_obs_t *obs);

/* The static (non-MTI) power of one bin: mean |I + jQ|^2 per sample over
 * every loopStep-th loop of the vertical pair and every RX; 0 for an
 * invalid geometry or a loopStep of 0. */
float l3_channels_static_power(const l3_channel_frame_t *frame, uint32_t localBin,
                               uint32_t loopStep);

/* A moving target's channels at one bin, for l3_angle_estimate: each
 * (tx, rx)'s residual summed over the loops with the per-loop Doppler phase
 * (lag1PhaseRad) unwound. An invalid geometry leaves out zeroed. */
void l3_channels_snapshot(const l3_channel_frame_t *frame, uint32_t localBin, float lag1PhaseRad,
                          float radialVelocityMps, l3_angle_snapshot_t *out);

/* A static target's channels (the ball on its tee): the raw samples summed
 * over the loops. An invalid geometry leaves out zeroed. */
void l3_channels_snapshot_static(const l3_channel_frame_t *frame, uint32_t localBin,
                                 l3_angle_snapshot_t *out);

#endif /* L3_CHANNELS_H */
