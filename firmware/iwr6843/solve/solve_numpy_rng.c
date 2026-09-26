/* Bit-exact numpy SeedSequence + PCG64 + Generator.choice(n,2,replace=False)
 * port. See solve_numpy_rng.h for what is and is not ported, and why.
 */
#include "solve_numpy_rng.h"

/* ---- SeedSequence (pool_size=4, empty spawn_key) --------------------------
 * numpy/random/bit_generator.pyx: hashmix()/mix()/mix_entropy()/
 * generate_state(). Constants are numpy's literal INIT_A/MULT_A/INIT_B/
 * MULT_B/MIX_MULT_L/MIX_MULT_R/XSHIFT.
 */
#define SOLVE_NUMPY_RNG_INIT_A    0x43b0d7e5u
#define SOLVE_NUMPY_RNG_MULT_A    0x931e8875u
#define SOLVE_NUMPY_RNG_INIT_B    0x8b51f9ddu
#define SOLVE_NUMPY_RNG_MULT_B    0x58f38dedu
#define SOLVE_NUMPY_RNG_MIX_L     0xca01f9ddu
#define SOLVE_NUMPY_RNG_MIX_R     0x4973f715u
#define SOLVE_NUMPY_RNG_XSHIFT    16u

static uint32_t solve_numpy_rng_hashmix(uint32_t value, uint32_t *hashConst)
{
    value ^= *hashConst;
    *hashConst = (*hashConst) * SOLVE_NUMPY_RNG_MULT_A;
    value = value * (*hashConst);
    value ^= value >> SOLVE_NUMPY_RNG_XSHIFT;
    return value;
}

static uint32_t solve_numpy_rng_mix(uint32_t x, uint32_t y)
{
    uint32_t result = (SOLVE_NUMPY_RNG_MIX_L * x) - (SOLVE_NUMPY_RNG_MIX_R * y);

    result ^= result >> SOLVE_NUMPY_RNG_XSHIFT;
    return result;
}

/* numpy's _int_to_uint32_array(seed) for a scalar non-negative integer,
 * truncated to the 64-bit seeds this project ever passes: seed==0 yields
 * one word (0); otherwise the low word, then the high word only if it is
 * non-zero (numpy's while-n>0 loop stops emitting words once n reaches 0). */
static void solve_numpy_rng_seed_pool(uint64_t seed, uint32_t pool[4])
{
    uint32_t entropy[2];
    uint32_t entropyLen;
    uint32_t hashConst = SOLVE_NUMPY_RNG_INIT_A;
    uint32_t i;
    uint32_t iSrc;
    uint32_t iDst;

    entropy[0] = (uint32_t)(seed & 0xFFFFFFFFu);
    entropy[1] = (uint32_t)(seed >> 32u);
    entropyLen = (entropy[1] != 0u) ? 2u : 1u;

    for (i = 0u; i < 4u; i++) {
        uint32_t v = (i < entropyLen) ? entropy[i] : 0u;

        pool[i] = solve_numpy_rng_hashmix(v, &hashConst);
    }
    /* "Mix all bits together so late bits can affect earlier bits." --
     * bit_generator.pyx's mix_entropy(), second loop. */
    for (iSrc = 0u; iSrc < 4u; iSrc++) {
        for (iDst = 0u; iDst < 4u; iDst++) {
            if (iSrc != iDst) {
                pool[iDst] = solve_numpy_rng_mix(
                    pool[iDst], solve_numpy_rng_hashmix(pool[iSrc], &hashConst));
            }
        }
    }
    /* mix_entropy()'s third loop (entropy words beyond the pool size) never
     * runs here: entropyLen <= 2 always, well under pool_size=4. */
}

static void solve_numpy_rng_generate_state_u64(const uint32_t pool[4], uint64_t out[4])
{
    uint32_t hashConst = SOLVE_NUMPY_RNG_INIT_B;
    uint32_t words[8];
    uint32_t i;

    for (i = 0u; i < 8u; i++) {
        uint32_t dataVal = pool[i % 4u];

        dataVal ^= hashConst;
        hashConst = hashConst * SOLVE_NUMPY_RNG_MULT_B;
        dataVal = dataVal * hashConst;
        dataVal ^= dataVal >> SOLVE_NUMPY_RNG_XSHIFT;
        words[i] = dataVal;
    }
    for (i = 0u; i < 4u; i++) {
        /* '<u4' view '<u8': low word first (little-endian pair). */
        out[i] = ((uint64_t)words[2u * i + 1u] << 32u) | (uint64_t)words[2u * i];
    }
}

/* ---- PCG64, emulated 128-bit math (portable C99, no __int128) ------------
 * numpy/random/src/pcg64/pcg64.h's #else (PCG_EMULATED_128BIT_MATH) branch,
 * ported line for line: pcg128_add/_mult/_mult64, pcg_setseq_128_step_r,
 * pcg_output_xsl_rr_128_64, pcg_setseq_128_srandom_r,
 * pcg_setseq_128_xsl_rr_64_random_r.
 */
#define SOLVE_NUMPY_RNG_MULT_HI 2549297995355413924ULL
#define SOLVE_NUMPY_RNG_MULT_LO 4865540595714422341ULL

static void solve_numpy_rng_mul64(uint64_t x, uint64_t y, uint64_t *hi, uint64_t *lo)
{
    uint64_t x0 = x & 0xFFFFFFFFu;
    uint64_t x1 = x >> 32u;
    uint64_t y0 = y & 0xFFFFFFFFu;
    uint64_t y1 = y >> 32u;
    uint64_t w0 = x0 * y0;
    uint64_t t = x1 * y0 + (w0 >> 32u);
    uint64_t w1 = t & 0xFFFFFFFFu;
    uint64_t w2 = t >> 32u;

    w1 += x0 * y1;
    *lo = x * y;
    *hi = x1 * y1 + w2 + (w1 >> 32u);
}

static void solve_numpy_rng_step(SolveNumpyRng *rng)
{
    uint64_t prodHi;
    uint64_t prodLo;
    uint64_t h1 = (rng->stateHi * SOLVE_NUMPY_RNG_MULT_LO) +
                  (rng->stateLo * SOLVE_NUMPY_RNG_MULT_HI);
    uint64_t sumLo;
    uint64_t sumHi;

    solve_numpy_rng_mul64(rng->stateLo, SOLVE_NUMPY_RNG_MULT_LO, &prodHi, &prodLo);
    prodHi += h1;
    /* + inc, mod 2**128 */
    sumLo = prodLo + rng->incLo;
    sumHi = prodHi + rng->incHi + ((sumLo < rng->incLo) ? 1u : 0u);
    rng->stateLo = sumLo;
    rng->stateHi = sumHi;
}

static uint64_t solve_numpy_rng_rotr64(uint64_t value, uint32_t rot)
{
    rot &= 63u;
    return (value >> rot) | (value << ((64u - rot) & 63u));
}

void solve_numpy_rng_seed(SolveNumpyRng *rng, uint64_t seed)
{
    uint32_t pool[4];
    uint64_t val[4];
    uint64_t initStateHi;
    uint64_t initStateLo;
    uint64_t initSeqHi;
    uint64_t initSeqLo;
    uint64_t carry;

    solve_numpy_rng_seed_pool(seed, pool);
    solve_numpy_rng_generate_state_u64(pool, val);
    initStateHi = val[0];
    initStateLo = val[1];
    initSeqHi = val[2];
    initSeqLo = val[3];

    rng->stateHi = 0u;
    rng->stateLo = 0u;
    /* inc = (initseq << 1) | 1, as a 128-bit shift. */
    rng->incHi = (initSeqHi << 1u) | (initSeqLo >> 63u);
    rng->incLo = (initSeqLo << 1u) | 1u;
    solve_numpy_rng_step(rng);
    carry = (rng->stateLo + initStateLo < initStateLo) ? 1u : 0u;
    rng->stateLo = rng->stateLo + initStateLo;
    rng->stateHi = rng->stateHi + initStateHi + carry;
    solve_numpy_rng_step(rng);
    rng->hasUint32 = 0u;
    rng->uinteger = 0u;
}

static uint64_t solve_numpy_rng_next64(SolveNumpyRng *rng)
{
    solve_numpy_rng_step(rng);
    return solve_numpy_rng_rotr64(rng->stateHi ^ rng->stateLo, (uint32_t)(rng->stateHi >> 58u));
}

static uint32_t solve_numpy_rng_next32(SolveNumpyRng *rng)
{
    uint64_t next;

    if (rng->hasUint32) {
        rng->hasUint32 = 0u;
        return rng->uinteger;
    }
    next = solve_numpy_rng_next64(rng);
    rng->hasUint32 = 1u;
    rng->uinteger = (uint32_t)(next >> 32u);
    return (uint32_t)(next & 0xFFFFFFFFu);
}

/* ---- random_bounded_uint64 (32-bit-range path only) -----------------------
 * distributions.c's random_bounded_uint64 dispatch for rng <= 0xFFFFFFFE,
 * and bounded_lemire_uint32's rejection loop. off is always 0 for this
 * stage's callers, so it is not threaded through. */
static uint32_t solve_numpy_rng_bounded_lemire_uint32(SolveNumpyRng *rng, uint32_t rangeIncl)
{
    uint32_t rngExcl = rangeIncl + 1u;
    uint64_t m = (uint64_t)solve_numpy_rng_next32(rng) * (uint64_t)rngExcl;
    uint32_t leftover = (uint32_t)(m & 0xFFFFFFFFu);

    if (leftover < rngExcl) {
        uint32_t threshold = (uint32_t)((0xFFFFFFFFu - rangeIncl) % rngExcl);

        while (leftover < threshold) {
            m = (uint64_t)solve_numpy_rng_next32(rng) * (uint64_t)rngExcl;
            leftover = (uint32_t)(m & 0xFFFFFFFFu);
        }
    }
    return (uint32_t)(m >> 32u);
}

static uint64_t solve_numpy_rng_bounded_uint64(SolveNumpyRng *rng, uint64_t rangeIncl)
{
    if (rangeIncl == 0u) {
        return 0u;
    }
    if (rangeIncl <= 0xFFFFFFFEu) {
        return (uint64_t)solve_numpy_rng_bounded_lemire_uint32(rng, (uint32_t)rangeIncl);
    }
    if (rangeIncl == 0xFFFFFFFFu) {
        return (uint64_t)solve_numpy_rng_next32(rng);
    }
    /* rangeIncl > 2**32-1: not reachable from solve_numpy_rng_pair() with
     * this project's buffer limits -- see solve_numpy_rng.h. Documented
     * fallback rather than a silent wrong answer. */
    return 0u;
}

/* ---- Generator.choice(n, 2, replace=False), pop_size <= 10000 path -------
 * _generator.pyx's Floyd's-algorithm branch, specialised to size=2 (this
 * stage's only use): set_size = 1 + _gen_mask(uint64(1.2*2)) = 1 + 3 = 4,
 * fixed at compile time since size never varies here. */
void solve_numpy_rng_pair(void *ctx, uint32_t n, uint32_t *i, uint32_t *j)
{
    SolveNumpyRng *rng = (SolveNumpyRng *)ctx;
    const uint64_t mask = 3u;   /* _gen_mask(2) == 3 */
    uint64_t hashSet[4];
    uint64_t idx[2];
    uint32_t k;
    uint32_t shuffleIdx;
    uint64_t tmp;

    hashSet[0] = (uint64_t)-1;
    hashSet[1] = (uint64_t)-1;
    hashSet[2] = (uint64_t)-1;
    hashSet[3] = (uint64_t)-1;

    for (k = n - 2u; k < n; k++) {
        uint64_t val = solve_numpy_rng_bounded_uint64(rng, (uint64_t)k);
        uint64_t loc = val & mask;

        while (hashSet[loc] != (uint64_t)-1 && hashSet[loc] != val) {
            loc = (loc + 1u) & mask;
        }
        if (hashSet[loc] == (uint64_t)-1) {
            hashSet[loc] = val;
            idx[k - (n - 2u)] = val;
        } else {
            loc = (uint64_t)k & mask;
            while (hashSet[loc] != (uint64_t)-1) {
                loc = (loc + 1u) & mask;
            }
            hashSet[loc] = (uint64_t)k;
            idx[k - (n - 2u)] = (uint64_t)k;
        }
    }
    /* choice()'s default shuffle=True: _shuffle_int(n=2, first=1, idx). */
    shuffleIdx = (uint32_t)solve_numpy_rng_bounded_uint64(rng, 1u);
    tmp = idx[shuffleIdx];
    idx[shuffleIdx] = idx[1];
    idx[1] = tmp;

    *i = (uint32_t)idx[0];
    *j = (uint32_t)idx[1];
}
