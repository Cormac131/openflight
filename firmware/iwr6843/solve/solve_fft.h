/* Windowed FFT stage: the Task-4 compute probe for the on-chip solve.
 *
 * Port of the shared primitive behind lcmf.py's two np.fft.fft call sites
 * that operate on a real capture (lcmf.py:502 in _prepared_fft, and the
 * synthetic-tone path at lcmf.py:547 in _fast_design): apply a Hann window
 * along the last axis, zero-pad to n_fft, and take the complex DFT. Both
 * sites use np.hanning(n_samples) and np.fft.fft(..., n=n_fft, axis=-1)
 * with n_fft=512 (the module default) -- this stage ports exactly that,
 * independent of the per-row values (raw capture vs. synthesized tones)
 * lcmf.py multiplies into the window beforehand.
 *
 * Struct-in/struct-out, no globals, no malloc -- see solve_fft.c for the
 * DSPLIB-vs-portable-reference seam and, importantly, exactly what is and
 * is not verified by the host test at tests/test_iwr6843_solve_fft.py.
 */
#ifndef L3_SOLVE_FFT_H
#define L3_SOLVE_FFT_H

#include <stdint.h>

/* One canonical snapshot is 2 TX blocks x n_rx elements, concatenated by
 * doa.canonicalize_tx_blocks (doa.py:140-151, "8-element snapshot" per the
 * docstring at doa.py:168). n_rx is 4 on every geometry this project has
 * configured, so 8 rows covers the real cube shape with no slack spent on
 * a hypothetical wider array. */
#define SOLVE_FFT_MAX_ROWS    8U

/* radar_geometry.n_samples: 128 is the sample count every chirp config in
 * this project uses (dump.py:542 default; tracking.py:365 branches on
 * "geo.n_samples >= 128" as the only value seen). */
#define SOLVE_FFT_MAX_SAMPLES 128U

/* lcmf.py's n_fft default at both call sites (_prepared_fft's `n_fft: int
 * = 512` keyword default, and _fast_design's positional n_fft passed the
 * same constant by its only caller, _fast_estimates). */
#define SOLVE_FFT_MAX_N       512U

#define SOLVE_FFT_OK          0U
#define SOLVE_FFT_ERROR       1U

typedef struct {
    uint32_t nRows;     /* rows to transform, 1..SOLVE_FFT_MAX_ROWS */
    uint32_t nSamples;  /* samples per row before the window/pad, 1..SOLVE_FFT_MAX_SAMPLES */
    uint32_t nFft;      /* FFT length, power of two, nSamples <= nFft <= SOLVE_FFT_MAX_N */
    float    real[SOLVE_FFT_MAX_ROWS][SOLVE_FFT_MAX_SAMPLES];
    float    imag[SOLVE_FFT_MAX_ROWS][SOLVE_FFT_MAX_SAMPLES];
} SolveFftRequest;

typedef struct {
    uint32_t status;    /* SOLVE_FFT_OK / SOLVE_FFT_ERROR */
    uint32_t nRows;
    uint32_t nFft;
    float    real[SOLVE_FFT_MAX_ROWS][SOLVE_FFT_MAX_N];
    float    imag[SOLVE_FFT_MAX_ROWS][SOLVE_FFT_MAX_N];
} SolveFftResult;

/* Windows every row with np.hanning(nSamples), zero-pads to nFft, and takes
 * the forward complex DFT (numpy's -2*pi*i*k*n/N convention, unnormalized)
 * of each row independently. Returns SOLVE_FFT_OK/SOLVE_FFT_ERROR; also
 * mirrored in res->status so a caller that only looked at the result record
 * (as the DSS solve dispatch will) still sees it. */
uint32_t solve_fft_apply(const SolveFftRequest *req, SolveFftResult *res);

#endif /* L3_SOLVE_FFT_H */
