/* Bit-exact port of numpy's default_rng(seed).choice(n, 2, replace=False) --
 * the exact draw source tracking.find_ball_from_power() uses for its RANSAC
 * loop (tracking.py:369-370). Ported for Task 5 of the on-chip solve plan
 * because l3track_rng_pair (track_select.c's xorshift32 pair source) does
 * NOT reproduce numpy's draw sequence -- see solve_tracking.c's file banner,
 * item 1, for the empirical finding that motivated this file.
 *
 * Verified bit-exact against numpy 2.4.6 (installed in this repo's .venv;
 * source cross-checked against the numpy/numpy GitHub tag v2.4.0, whose
 * choice()/SeedSequence/PCG64 implementations are unchanged from 2.4.6's) --
 * see tests/test_iwr6843_solve_numpy_rng.py for the equivalence tests this
 * claim rests on, and its pinned-numpy-version guard test.
 *
 * WHAT IS PORTED, PRECISELY (all confirmed by reading numpy's actual source,
 * not assumed -- see the review URLs in solve_tracking.c's banner):
 *   - numpy.random.SeedSequence(entropy=seed) at the default pool_size=4,
 *     empty spawn_key -- the mix_entropy()/hashmix()/mix() hash exactly as
 *     numpy/random/bit_generator.pyx defines them.
 *   - SeedSequence.generate_state(4, dtype=np.uint64) -- the pool-cycling
 *     hash exactly as bit_generator.pyx's generate_state() defines it.
 *   - PCG64's seeding (pcg64_set_seed -> pcg_setseq_128_srandom_r) and
 *     stepping (pcg_setseq_128_xsl_rr_64_random_r / the XSL-RR 128/64
 *     output function) -- numpy/random/src/pcg64/pcg64.h's EMULATED-128-bit
 *     path (this file never assumes __int128; it always does the manual
 *     32-bit-halves multiply, matching pcg64.h's own portable fallback, so
 *     the C6000/C7000 DSP target -- which has no 128-bit integer type --
 *     gets the identical arithmetic the host's compiled-with-__int128 numpy
 *     also produces; they are mathematically the same computation mod
 *     2**128 either way).
 *   - PCG64's next_uint32 buffering (pcg64_next32: one next_uint64 call
 *     produces two next_uint32 results, low word first).
 *   - random_bounded_uint64's Lemire-rejection path for a range fitting in
 *     32 bits (distributions.c's bounded_lemire_uint32, called via
 *     next_uint32 -- NOT next_uint64; this matters, and was confirmed by
 *     reading random_bounded_uint64's actual dispatch, not assumed).
 *   - Generator.choice(n, 2, replace=False)'s actual algorithm for
 *     pop_size <= 10000 (always true here; the corpus's largest nOrder is
 *     160, and SOLVE_TRACKING_MAX_DETECTIONS is 2048): Floyd's algorithm
 *     with numpy's open-addressing hash-set rejection (_generator.pyx's
 *     choice(), the "else: # Floyd's algorithm" branch), followed by the
 *     default shuffle=True's single-swap _shuffle_int(size=2, first=1, ...).
 *     The size>10000-and-large-fraction "tail shuffle" branch is NOT ported
 *     -- this stage's n is never in that range (see solve_numpy_rng_pair()).
 *
 * NOT PORTED, DELIBERATELY: random_bounded_uint64's paths for a range that
 * does not fit in 32 bits (rng > 0xFFFFFFFE). n (the population size handed
 * to choice()) is find_ball_from_power's nOrder -- at most
 * SOLVE_TRACKING_MAX_DETECTIONS (2048) -- so j (= n-1 at most) never
 * approaches 2**32. solve_numpy_rng_pair() below has an explicit, documented
 * fallback for that case rather than silently mis-answering it; nothing in
 * this project's corpus or buffer limits can reach it.
 *
 * Struct-in/struct-out, no malloc, no globals -- same porting contract as
 * every other solve stage source file.
 */
#ifndef L3_SOLVE_NUMPY_RNG_H
#define L3_SOLVE_NUMPY_RNG_H

#include <stdint.h>

typedef struct {
    uint64_t stateHi;
    uint64_t stateLo;
    uint64_t incHi;
    uint64_t incLo;
    uint8_t  hasUint32;
    uint32_t uinteger;
} SolveNumpyRng;

/* Seeds *rng exactly as np.random.default_rng(seed) would (seed a
 * non-negative integer; this port supports the full 64-bit range, which
 * covers every seed this project's tests or firmware use -- always 1). */
void solve_numpy_rng_seed(SolveNumpyRng *rng, uint64_t seed);

/* Draws two distinct indices below n, bit-identical to
 * `first, second = np.random.default_rng(seed).choice(n, 2, replace=False)`
 * called repeatedly on the SAME Generator instance (i.e. this reproduces
 * find_ball_from_power's exact draw sequence across all `iterations` RANSAC
 * draws, not just the first one -- see the equivalence test's 2500-iteration
 * sequence check). Same signature as SolveTrackingPairFn / track_select.h's
 * L3TrackPairFn, so this can be passed directly wherever those are: pass
 * &rng cast to void* as ctx. */
void solve_numpy_rng_pair(void *ctx, uint32_t n, uint32_t *i, uint32_t *j);

#endif /* L3_SOLVE_NUMPY_RNG_H */
