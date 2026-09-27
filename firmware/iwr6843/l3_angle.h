/* IWR6843 angle estimation: azimuth and elevation of one target from the
 * virtual antenna channels at its range bin.
 *
 * Geometry (IWR6843 LEVM, OpenFlight enclosure rotated TX-above-RX):
 *   - RX0..RX3 form a lambda/2 line; the vertical TX pair (TX0 and TX2 of a
 *     three-TX loop, both TX of a two-TX loop) is 2 lambda apart on the same
 *     axis, so [txA.rx0..rx3, txB.rx0..rx3] is a uniform 8-element lambda/2
 *     ELEVATION array. The board's RX order is reversed relative to the
 *     calibration's physical order, so the vector is flipped before the
 *     per-element corrections apply, and a steering vector
 *     exp(j * pi * sin(theta) * m) then reads theta positive UP.
 *   - TX1 (the middle transmitter) is displaced lambda/2 from the TX0/TX2
 *     phase centre on the orthogonal axis, which the rotation makes
 *     horizontal: the phase of TX1 against that centre is -pi * sin(azimuth),
 *     azimuth positive RIGHT (TX1 sits physically left of the centre).
 *
 * TDM: the transmitters fire one after another, tau apart, so a moving
 * target rotates each TX block by 4 pi v tau / lambda per chirp. The
 * per-loop phase the observation layer measures (lag-1 autocorrelation) is
 * that rotation times the TX count, aliased to +/- pi; the coarse radial
 * velocity from the range walk resolves which alias, and every channel is
 * referred to the loop's first chirp before angles are read.
 *
 * Elevation: conventional beamforming (Bartlett) on the 8 corrected
 * elements over +/- 40 degrees in 0.5 degree steps with parabolic refinement;
 * the peak-to-mean power ratio is the quality. Azimuth: the coherent mean
 * over RX of TX1 against the pair centre; |mean| is the quality.
 * Pure C, no hardware.
 */
#ifndef L3_ANGLE_H
#define L3_ANGLE_H

#include <stdint.h>

#include "l3_frames.h"

#define L3_ANGLE_MAX_TX        3U
#define L3_ANGLE_MAX_RX        4U
#define L3_ANGLE_MAX_CHANNELS  (L3_ANGLE_MAX_TX * L3_ANGLE_MAX_RX)
#define L3_ANGLE_MAX_VIRTUAL   8U
#define L3_ANGLE_GRID_STEPS    161U     /* -40..+40 degrees */
#define L3_ANGLE_GRID_STEP_RAD 0.00872665F /* 0.5 degree */
#define L3_ANGLE_WAVELENGTH_M  0.00484F
#define L3_ANGLE_BASELINE_LAMBDA 0.5F   /* TX1 off the TX0/TX2 centre */
#define L3_ANGLE_PI            3.14159265F

typedef struct {
    float re;
    float im;
} l3_cpx_t;

/* One target's channel snapshot: the complex value of each (tx, rx) at the
 * target's range bin, tx-major ([tx0.rx0..rx3, tx1.rx0.., tx2..]), all
 * loops already summed coherently so only the TDM chirp offsets remain. */
typedef struct {
    uint32_t ntx;
    uint32_t nrx;
    l3_cpx_t channel[L3_ANGLE_MAX_CHANNELS];
    float    lag1PhaseRad;       /* per-loop Doppler phase, aliased to +/- pi */
    float    radialVelocityMps;  /* coarse, unambiguous; positive away from the radar */
    float    chirpPeriodS;       /* TDM tau */
} l3_angle_snapshot_t;

/* Peak-to-mean ratio at which the elevation confidence reaches 1: an
 * 8-element Bartlett beam on a single clean source peaks near 8x its mean. */
#define L3_ANGLE_PEAK_RATIO_FULL 6.0F

typedef struct {
    float   azimuthRad;
    float   elevationRad;
    float   azimuthCoherence;    /* 0..1, |coherent mean| of the baseline phasor */
    float   elevationPeakRatio;  /* beamformer peak over mean power; 1 is flat */
    float   chirpPhaseRad;       /* TDM phase per chirp used for the correction */
    float   confidence;          /* 0..1: the elevation peak's sharpness, and the
                                  * azimuth coherence when azimuth was measured */
    uint8_t azimuthValid;
    uint8_t elevationValid;
} l3_angle_obs_t;

void l3_angle_snapshot_init(l3_angle_snapshot_t *snapshot, uint32_t ntx, uint32_t nrx);
/* The per-chirp TDM phase: the aliased per-loop phase unwrapped by the alias
 * that best matches the coarse radial velocity, divided by the TX count. */
float l3_angle_chirp_phase(float lag1PhaseRad, uint32_t ntx, float radialVelocityMps,
                           float chirpPeriodS);
/* Conventional beamforming on n (<= 8) elements already in physical order
 * and corrected. Returns the peak angle (radians, positive up) and the
 * peak-to-mean power ratio. */
float l3_angle_bartlett(const l3_cpx_t *elements, uint32_t n, float *peakRatio);
/* Both angles from a snapshot. Returns 1 when at least the elevation could be
 * estimated (two TX and at least two RX), else 0 with both flags clear.
 * Azimuth needs three TX. */
int32_t l3_angle_estimate(const l3_radar_cal_t *cal, const l3_angle_snapshot_t *snapshot,
                          l3_angle_obs_t *out);
/* 0..1 from a peak ratio (1 flat .. L3_ANGLE_PEAK_RATIO_FULL) and, when the
 * azimuth was measured, its coherence: the smaller of the two. */
float l3_angle_confidence(float elevationPeakRatio, float azimuthCoherence, uint8_t azimuthValid);
/* "angle az=1.20 el=-3.40 coh=0.91 peak=6.2 psi=0.35 conf=0.84 valid=ae" */
int32_t l3_angle_format(const l3_angle_obs_t *obs, char *out, uint32_t cap);

#endif /* L3_ANGLE_H */
