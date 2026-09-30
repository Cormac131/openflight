/* IWR6843 self-trigger front end over MTI residuals.
 *
 * l3_dump.c reduces each completed frame to one observation per range bin of
 * the watch region (integrated burst-MTI residual energy across every loop,
 * plus the lag-1 loop autocorrelation for Doppler). This module owns what the
 * trigger needs from those before any tracking: the watch region around the
 * destination bin, the adaptive noise floor the club track's targets are
 * extracted against (threshold = floor x snr), and a raw-input trace with a
 * per-bin maximum that say what the radar was offered.
 *
 * It fires nothing. The club track (l3_club_track.c) follows the club through
 * the region and its range-only impact (l3_impact.c) freezes the capture. The
 * range gate that once fired from here, on its own short track, was removed
 * on 2026-09-30: it held the tee's standing clutter and missed half the
 * recorded swings the club track catches.
 *
 * It touches no hardware, so tests/test_iwr6843_firmware_trigger.py builds it
 * with the host compiler and drives synthetic frames through it.
 *
 * Geometry: the clubhead approaches from short of the ball, so "toward the
 * tee" means a rising bin.
 */
#ifndef L3_TRIGGER_H
#define L3_TRIGGER_H

#include <stdint.h>

#include "l3_observation.h"

/* Watch-region limit. The region is at most one capture window. */
#define L3_TRIG_MAX_BINS          64U
/* Raw-input trace: the region's strongest bin, every frame it reaches
 * L3_TRIG_TRACE_RATIO times the floor (well under any snr worth arming
 * with), with its energy, strongest loop and loop-0 power. Answers "did the
 * radar see anything at all" after a missed swing. The board image keeps
 * fewer (L3_FEATURE_DEFS in the makefile) to fit DATA_RAM. */
#ifndef L3_TRIG_TRACE_DEPTH
#define L3_TRIG_TRACE_DEPTH       64U
#endif
#define L3_TRIG_TRACE_RATIO       2.0F
/* Noise-floor smoothing: floor += (median - floor) / 2^shift each frame. */
#define L3_TRIG_FLOOR_SHIFT       3U

/* Defaults for the optional triggerCfg parameters. */
#define L3_TRIG_DEFAULT_APPROACH_BINS 12U   /* ~0.56 m short of the tee */
#define L3_TRIG_DEFAULT_PAST_BINS     3U    /* ~0.14 m past it */
#define L3_TRIG_DEFAULT_STAT          L3_TRIG_STAT_PEAK

/* The detection statistic is the observation layer's (l3_observation.h). */
#define L3_TRIG_STAT_ENERGY L3_OBS_STAT_ENERGY
#define L3_TRIG_STAT_PEAK   L3_OBS_STAT_PEAK

typedef struct {
    uint32_t teeBin;        /* global range bin of the tee (default destination) */
    float    snr;           /* target threshold = floor * snr (>= 1) */
    uint32_t approachBins;  /* watch this many bins short of the destination */
    uint32_t pastBins;      /* ... and this many past it */
    uint32_t stat;          /* L3_TRIG_STAT_ENERGY or L3_TRIG_STAT_PEAK */
} l3_trig_cfg_t;

/* One range bin of one frame is the observation layer's l3_bin_obs_t. */
typedef l3_bin_obs_t l3_trig_obs_t;

/* One traced frame: the region's strongest bin by the configured statistic. */
typedef struct {
    uint32_t frame;
    uint16_t gap;           /* untraced frames since the previous entry */
    uint8_t  bin;           /* strongest global bin */
    uint8_t  dest;          /* destination global bin; dist = dest - bin */
    float    energy;
    float    peak;
    float    loop0;
    float    floor;         /* in the configured statistic's units */
    float    threshold;     /* floor x snr in force this frame */
    uint8_t  coherencePct;  /* |lag-1 autocorrelation| / energy */
} l3_trig_trace_t;

typedef struct {
    l3_trig_cfg_t cfg;
    float    floor;
    uint32_t frames;        /* frames observed since l3_trig_init */
    /* Raw-input trace and per-bin maximum since arming or the last clear. */
    uint32_t traceQuiet;
    uint32_t traceNext;
    uint32_t traceCount;
    l3_trig_trace_t trace[L3_TRIG_TRACE_DEPTH];
    uint32_t maxFirstBin;   /* region start the max-hold indices refer to */
    uint32_t maxBins;
    float    maxStat[L3_TRIG_MAX_BINS];
    uint32_t maxFrame[L3_TRIG_MAX_BINS];
} l3_trig_t;

/* Fill cfg with the defaults for the optional parameters. */
void l3_trig_cfg_defaults(l3_trig_cfg_t *cfg);
/* Reject a configuration the front end cannot run. Returns 0 when usable. */
int32_t l3_trig_cfg_check(const l3_trig_cfg_t *cfg);
/* Reset the floor, the frame count, the trace and the max-hold. */
void l3_trig_init(l3_trig_t *trig, const l3_trig_cfg_t *cfg);
/* Every bin this module speaks of is a GLOBAL range-FFT bin (0..127 on a
 * 128-point FFT), never a capture-window offset: the window start moves
 * between profiles and between the pre and post phases, and a tee bin read
 * in the wrong coordinates watched an empty stretch of air.
 *
 * Watch region for a frame whose window holds binCount bins from global bin
 * windowStart, around the destination teeBin (global). firstLocal is the
 * window offset to index the frame with; firstLocal + windowStart is the
 * global bin of obs[0]. Returns 0 when the tee is outside the window. */
int32_t l3_trig_region(const l3_trig_cfg_t *cfg, uint32_t teeBin, uint32_t windowStart,
                       uint32_t binCount, uint32_t *firstLocal, uint32_t *count);
/* Feed one frame: obs[i] describes global bin firstBin + i around the
 * destination teeBin (global; the configured tee or, when the ball detector
 * is followed, the ball's bin). Updates the floor, the trace and the
 * max-hold. */
void l3_trig_observe(l3_trig_t *trig, uint32_t frame, uint32_t teeBin, uint32_t firstBin,
                     const l3_trig_obs_t *obs, uint32_t count);
/* floor x snr: the level a target must reach this frame. */
float l3_trig_threshold(const l3_trig_t *trig);

/* Integer-only text (the R4F CLI printf may lack %f). Each returns the
 * length snprintf reports; cap counts the terminating NUL. */
int32_t l3_trig_format_summary(const l3_trig_t *trig, char *out, uint32_t cap);
int32_t l3_trig_format_config(const l3_trig_t *trig, char *out, uint32_t cap);

/* Raw-input trace. clear empties the trace and the max-hold only. */
void l3_trig_trace_clear(l3_trig_t *trig);
uint32_t l3_trig_trace_count(const l3_trig_t *trig);
int32_t l3_trig_trace_get(const l3_trig_t *trig, uint32_t index, l3_trig_trace_t *out);
int32_t l3_trig_format_trace_header(const l3_trig_t *trig, char *out, uint32_t cap);
int32_t l3_trig_format_trace(const l3_trig_trace_t *entry, char *out, uint32_t cap);
/* Max-hold for up to count region bins from index start: "trigmax b:stat@frame ...". */
int32_t l3_trig_format_maxhold(const l3_trig_t *trig, uint32_t start, uint32_t count,
                               char *out, uint32_t cap);

#endif /* L3_TRIGGER_H */
