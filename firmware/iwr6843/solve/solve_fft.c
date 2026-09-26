/* Windowed FFT stage. See solve_fft.h for the port this file implements.
 *
 * === The seam, and what it does and does not verify ===
 *
 * DSPLIB's C674x FFT (DSPF_sp_fftSPxSP) only exists for the cl6x/C674x
 * toolchain -- it is not something the host compiler can build, and the
 * harness (tests/test_iwr6843_solve_harness.py) compiles solve sources with
 * the HOST compiler so ctypes can drive them. A solve_fft.c that called
 * DSPLIB directly would therefore be untestable on this host at all: no
 * host build, no equivalence test, nothing until it happened to run on
 * silicon.
 *
 * The windowing, zero-padding, and per-row iteration in this file are
 * portable C99 -- the host compiler builds them and the equivalence test
 * exercises them for real. Only the raw N-point complex DFT sits behind a
 * two-implementation interface, solve_fft_transform(), selected by the
 * SOLVE_USE_DSPLIB macro (explicit, not `#ifdef __TI_COMPILER_VERSION__`,
 * so a reader does not have to know that detail of the toolchain to see
 * which path is active):
 *
 *   - SOLVE_USE_DSPLIB undefined (every host build, including the pytest
 *     harness): solve_fft_transform_reference() below, a plain iterative
 *     radix-2 Cooley-Tukey DFT computed in double precision.
 *   - SOLVE_USE_DSPLIB defined (the DSS/C674x build only, set by
 *     firmware/iwr6843/dss/makefile): solve_fft_transform_dsplib(), which
 *     calls DSPF_sp_fftSPxSP from TI's DSPLIB, per the plan's requirement
 *     to use DSPLIB rather than a hand-rolled FFT on the DSP.
 *
 * ****************************************************************************
 * * IMPORTANT: tests/test_iwr6843_solve_fft.py exercises ONLY the portable  *
 * * reference path (solve_fft_transform_reference), because that is the    *
 * * only path any host compiler can build. It proves the windowing,        *
 * * zero-padding, and reference DFT match np.fft.fft to the stated         *
 * * tolerance. It proves NOTHING about solve_fft_transform_dsplib() --     *
 * * that path is exercised by nothing in this repository. It is unverified *
 * * until it has actually run against a known input on the C674x and been *
 * * checked against the Python reference there. Do not treat a green host *
 * * test run as evidence the DSPLIB path is correct.                      *
 * ****************************************************************************
 *
 * The reference path deliberately computes in double precision (matching
 * numpy's float64 pipeline) and only rounds to float32 when writing the
 * result record, so the host tolerance measures the port's algorithmic
 * correctness rather than being swamped by float32 rounding that has
 * nothing to do with this port. DSPF_sp_fftSPxSP computes natively in
 * single precision on the DSP -- once it is exercised on silicon it should
 * be expected to show a different (larger) error profile against the
 * Python double-precision reference than the portable path does here, and
 * that on-chip tolerance has not yet been characterized or justified. That
 * characterization is exactly what running the parked l3fft on-chip
 * measurement step is for; see the Task 4 report.
 */
#include "solve_fft.h"

#include <math.h>
#include <string.h>

#if defined(SOLVE_USE_DSPLIB)
#include <ti/dsplib/dsplib.h>
#endif

/* Local pi constant rather than M_PI: -std=c99 does not guarantee M_PI is
 * defined by <math.h> (it is a POSIX/BSD extension, not ISO C99), and this
 * file must build clean under -Wall -Wextra -Werror on the host compiler. */
#define SOLVE_FFT_PI 3.14159265358979323846

static int solve_fft_is_pow2(uint32_t n)
{
    return (n != 0U) && ((n & (n - 1U)) == 0U);
}

/* np.hanning(n): 0.5 - 0.5*cos(2*pi*i/(n-1)) for i in [0, n); np.hanning(1)
 * is the single-element array [1.0] (numpy special-cases n<=1 rather than
 * dividing by zero). Computed in double to match numpy's float64 window. */
static void solve_fft_hanning(uint32_t n, double *window)
{
    uint32_t i;

    if (n == 0U) {
        return;
    }
    if (n == 1U) {
        window[0] = 1.0;
        return;
    }
    for (i = 0U; i < n; i++) {
        window[i] = 0.5 - 0.5 * cos(2.0 * SOLVE_FFT_PI * (double)i / (double)(n - 1U));
    }
}

/* The portable reference DFT (and its two small helpers just below) is only
 * ever called from solve_fft_transform() when SOLVE_USE_DSPLIB is NOT
 * defined -- see that function further down. Compiling it in anyway on the
 * DSS build, where it would be unreferenced dead code, trips
 * --emit_warnings_as_errors (#179-D "declared but never referenced") on the
 * C674x compiler, so it is excluded from that build entirely rather than
 * left for the linker to drop. */
#if !defined(SOLVE_USE_DSPLIB)

static uint32_t solve_fft_ilog2(uint32_t n)
{
    uint32_t bits = 0U;

    while ((1U << bits) < n) {
        bits++;
    }
    return bits;
}

static uint32_t solve_fft_bit_reverse(uint32_t value, uint32_t bits)
{
    uint32_t result = 0U;
    uint32_t i;

    for (i = 0U; i < bits; i++) {
        result = (result << 1) | (value & 1U);
        value >>= 1;
    }
    return result;
}

/* Portable reference: iterative radix-2 decimation-in-time Cooley-Tukey,
 * matching numpy's forward-transform sign convention
 * X[k] = sum_n x[n] * exp(-2*pi*i*k*n/N), unnormalized. In place, double
 * precision. n must be a power of two (checked by the caller). */
static void solve_fft_transform_reference(double *re, double *im, uint32_t n)
{
    uint32_t bits = solve_fft_ilog2(n);
    uint32_t i;
    uint32_t size;

    for (i = 0U; i < n; i++) {
        uint32_t j = solve_fft_bit_reverse(i, bits);
        if (j > i) {
            double tmp = re[i];
            re[i] = re[j];
            re[j] = tmp;
            tmp = im[i];
            im[i] = im[j];
            im[j] = tmp;
        }
    }

    for (size = 2U; size <= n; size <<= 1) {
        uint32_t half = size >> 1;
        double theta = -2.0 * SOLVE_FFT_PI / (double)size;
        double wr = cos(theta);
        double wi = sin(theta);
        uint32_t start;

        for (start = 0U; start < n; start += size) {
            double curR = 1.0;
            double curI = 0.0;
            uint32_t k;

            for (k = 0U; k < half; k++) {
                uint32_t idxA = start + k;
                uint32_t idxB = idxA + half;
                double tr = curR * re[idxB] - curI * im[idxB];
                double ti = curR * im[idxB] + curI * re[idxB];
                double nextR;
                double nextI;

                re[idxB] = re[idxA] - tr;
                im[idxB] = im[idxA] - ti;
                re[idxA] += tr;
                im[idxA] += ti;

                nextR = curR * wr - curI * wi;
                nextI = curR * wi + curI * wr;
                curR = nextR;
                curI = nextI;
            }
        }
    }
}

#endif /* !SOLVE_USE_DSPLIB */

#if defined(SOLVE_USE_DSPLIB)

/* Twiddle-factor generation for DSPF_sp_fftSPxSP, adapted from TI's own
 * fft_sp_ex example (dsplib_c674x_3_4_0_0/examples/fft_sp_ex/fft_example_sp.c,
 * gen_twiddle_fft_sp) -- DSPLIB ships the FFT kernel itself as a compiled
 * library, not this generator, so callers are expected to bring their own
 * copy of it. UNVERIFIED, per the file banner above: nothing in this repo
 * builds or runs this function, since it needs the C674x toolchain.
 * `w` must hold at least 2*n floats. */
static void solve_fft_gen_twiddle(float *w, int n)
{
    int i;
    int j;
    int k;

    for (j = 1, k = 0; j <= (n >> 2); j = j << 2) {
        for (i = 0; i < (n >> 2); i += j) {
            double theta1 = 2.0 * SOLVE_FFT_PI * (double)i / (double)n;
            double theta2 = 4.0 * SOLVE_FFT_PI * (double)i / (double)n;
            double theta3 = 6.0 * SOLVE_FFT_PI * (double)i / (double)n;

            w[k]     = (float)cos(theta1);
            w[k + 1] = (float)sin(theta1);
            w[k + 2] = (float)cos(theta2);
            w[k + 3] = (float)sin(theta2);
            w[k + 4] = (float)cos(theta3);
            w[k + 5] = (float)sin(theta3);
            k += 6;
        }
    }
}

/* 64-entry radix-4 bit-reverse table, straight from TI's fft_sp_ex example
 * (see solve_fft_gen_twiddle's comment) -- DSPF_sp_fftSPxSP indexes this
 * internally regardless of the requested FFT length. */
static unsigned char solve_fft_brev[64] = {
    0x0,  0x20, 0x10, 0x30, 0x8,  0x28, 0x18, 0x38,
    0x4,  0x24, 0x14, 0x34, 0xc,  0x2c, 0x1c, 0x3c,
    0x2,  0x22, 0x12, 0x32, 0xa,  0x2a, 0x1a, 0x3a,
    0x6,  0x26, 0x16, 0x36, 0xe,  0x2e, 0x1e, 0x3e,
    0x1,  0x21, 0x11, 0x31, 0x9,  0x29, 0x19, 0x39,
    0x5,  0x25, 0x15, 0x35, 0xd,  0x2d, 0x1d, 0x3d,
    0x3,  0x23, 0x13, 0x33, 0xb,  0x2b, 0x1b, 0x3b,
    0x7,  0x27, 0x17, 0x37, 0xf,  0x2f, 0x1f, 0x3f
};

/* DSS/C674x path. Interleaves re/im into DSPLIB's expected format, runs
 * DSPF_sp_fftSPxSP with n_min=2 (correct for any power-of-two N regardless
 * of whether N is also a power of four -- the plan's N=512 is not), and
 * de-interleaves the (already normal-order) output. UNVERIFIED: see the
 * file banner. Not reachable from any host build or test. */
static void solve_fft_transform_dsplib(double *re, double *im, uint32_t n)
{
    float x[2U * SOLVE_FFT_MAX_N];
    float y[2U * SOLVE_FFT_MAX_N];
    float w[2U * SOLVE_FFT_MAX_N];
    uint32_t i;

    for (i = 0U; i < n; i++) {
        x[2U * i]      = (float)re[i];
        x[2U * i + 1U] = (float)im[i];
    }

    solve_fft_gen_twiddle(w, (int)n);
    DSPF_sp_fftSPxSP((int)n, x, w, y, solve_fft_brev, 2, 0, (int)n);

    for (i = 0U; i < n; i++) {
        re[i] = (double)y[2U * i];
        im[i] = (double)y[2U * i + 1U];
    }
}

#endif /* SOLVE_USE_DSPLIB */

static void solve_fft_transform(double *re, double *im, uint32_t n)
{
#if defined(SOLVE_USE_DSPLIB)
    solve_fft_transform_dsplib(re, im, n);
#else
    solve_fft_transform_reference(re, im, n);
#endif
}

uint32_t solve_fft_apply(const SolveFftRequest *req, SolveFftResult *res)
{
    double window[SOLVE_FFT_MAX_SAMPLES];
    uint32_t row;
    uint32_t i;

    memset(res, 0, sizeof(*res));
    res->status = SOLVE_FFT_ERROR;

    if (req->nRows == 0U || req->nRows > SOLVE_FFT_MAX_ROWS) {
        return SOLVE_FFT_ERROR;
    }
    if (req->nSamples == 0U || req->nSamples > SOLVE_FFT_MAX_SAMPLES) {
        return SOLVE_FFT_ERROR;
    }
    if (req->nFft == 0U || req->nFft > SOLVE_FFT_MAX_N) {
        return SOLVE_FFT_ERROR;
    }
    if (!solve_fft_is_pow2(req->nFft)) {
        return SOLVE_FFT_ERROR;
    }
    if (req->nSamples > req->nFft) {
        /* np.fft.fft(x, n=n_fft) would truncate x to n_fft samples here
         * instead of padding; every real call site pads (n_fft=512 >=
         * n_samples=128), so treat the truncating case as out of scope
         * rather than silently matching a behavior this port never needs. */
        return SOLVE_FFT_ERROR;
    }

    solve_fft_hanning(req->nSamples, window);

    for (row = 0U; row < req->nRows; row++) {
        double re[SOLVE_FFT_MAX_N];
        double im[SOLVE_FFT_MAX_N];

        memset(re, 0, sizeof(re));
        memset(im, 0, sizeof(im));

        for (i = 0U; i < req->nSamples; i++) {
            re[i] = (double)req->real[row][i] * window[i];
            im[i] = (double)req->imag[row][i] * window[i];
        }
        /* Samples from nSamples..nFft-1 stay zero: the zero-pad. */

        solve_fft_transform(re, im, req->nFft);

        for (i = 0U; i < req->nFft; i++) {
            res->real[row][i] = (float)re[i];
            res->imag[row][i] = (float)im[i];
        }
    }

    res->status = SOLVE_FFT_OK;
    res->nRows = req->nRows;
    res->nFft = req->nFft;
    return SOLVE_FFT_OK;
}
