/* IWR6843 radar observation layer: from per-bin residual measurements to
 * ranked target observations.
 *
 * l3_dump.c reduces each completed frame to one l3_bin_obs_t per range bin of
 * a watched region (burst-MTI residual energy over all loops, the strongest
 * loop, loop 0, and the lag-1 loop autocorrelation). This layer owns what is
 * done with those before anyone decides anything: the detection statistic,
 * the adaptive noise floor, target extraction (local maxima above the floor)
 * with sub-bin range, signal quality, the Doppler observation and candidate
 * ranking. It does not decide whether impact happened (l3_trigger.c) or
 * where the club is going (l3_club_track.c).
 *
 * Every bin is a GLOBAL range-FFT bin. Angle fields exist so trackers can be
 * written against them now; they read invalid until the azimuth/elevation
 * estimation lands. Pure C, no hardware.
 */
#ifndef L3_OBSERVATION_H
#define L3_OBSERVATION_H

#include <stdint.h>

#define L3_OBS_MAX_BINS      64U
#define L3_OBS_MAX_TARGETS   8U
/* Carrier wavelength for the Doppler readout: the 60 GHz profiles sweep
 * 60.3-63.8 GHz; 62 GHz is close enough for a diagnostic. */
#define L3_OBS_WAVELENGTH_M  0.00484F
#define L3_OBS_PI            3.14159265F
/* A floor never falls below this, so ratios stay finite. */
#define L3_OBS_FLOOR_MIN     1.0F

/* Which per-bin statistic the floor and the threshold use. A fast clubhead can
 * be in a bin for only part of a frame, so the strongest single loop is the
 * sensitive choice; the energy over every loop is the steadier one. */
enum {
    L3_OBS_STAT_ENERGY = 0,
    L3_OBS_STAT_PEAK = 1
};

/* One range bin of one frame, summed over the vertical TX pair and all RX. */
typedef struct {
    float energy;   /* residual energy over every loop */
    float peak;     /* strongest single loop's residual power */
    float loop0;    /* loop 0 alone: the probe the first detector used */
    float r1Re;     /* lag-1 residual autocorrelation */
    float r1Im;
} l3_bin_obs_t;

typedef struct {
    uint32_t stat;          /* L3_OBS_STAT_* */
    float    snr;           /* a target needs stat >= floor * snr */
    float    loopPeriodS;   /* for the Doppler readout; 0 disables it */
} l3_obs_params_t;

/* One extracted target. */
typedef struct {
    uint32_t frame;
    uint32_t timestampUs;
    uint8_t  peakBin;         /* global bin of the maximum */
    float    rangeBin;        /* global, sub-bin: stat-weighted over peak +/- 1 */
    float    energy;
    float    peak;
    float    loop0;
    float    stat;            /* the configured statistic at the peak bin */
    float    snr;             /* stat / floor */
    float    coherence;       /* |r1| / energy, 0..1 */
    float    r1Re;
    float    r1Im;
    float    dopplerPhaseRad; /* lag-1 phase, -pi..pi */
    float    dopplerAliasMps; /* aliased at +/- wavelength / (4 T) */
    float    azimuthRad;      /* invalid until angle estimation exists */
    float    elevationRad;
    uint8_t  anglesValid;
    float    confidence;      /* 0..1 from margin over threshold and coherence */
} l3_target_obs_t;

/* The configured statistic of one bin. */
float l3_obs_stat(uint32_t stat, const l3_bin_obs_t *obs);
/* Median of the statistic over count bins (count <= L3_OBS_MAX_BINS). */
float l3_obs_median(uint32_t stat, const l3_bin_obs_t *obs, uint32_t count);
/* Adaptive floor: seed from the first median, then floor += (median - floor)
 * / 2^shift, never under L3_OBS_FLOOR_MIN. The median of a region is noise
 * even while a club occupies a few bins of it. */
void l3_obs_floor_update(float *floor, uint32_t stat, const l3_bin_obs_t *obs,
                         uint32_t count, uint32_t shift);
/* Lag-1 phase to apparent radial velocity (m/s); 0 without a loop period. */
float l3_obs_velocity(float r1Re, float r1Im, float loopPeriodS);
/* Extract up to maxOut targets from obs[i] (global bin firstBin + i): local
 * maxima of the statistic at or above floor * params->snr, strongest first.
 * Returns how many were written. */
uint32_t l3_obs_extract(const l3_obs_params_t *params, uint32_t frame, uint32_t timestampUs,
                        uint32_t firstBin, const l3_bin_obs_t *obs, uint32_t count,
                        float floor, l3_target_obs_t *out, uint32_t maxOut);
/* "obs frame=.. bin=48.3 snr=12.1 coh=88 v=-3.20 conf=0.71" */
int32_t l3_obs_format_target(const l3_target_obs_t *target, char *out, uint32_t cap);

#endif /* L3_OBSERVATION_H */
