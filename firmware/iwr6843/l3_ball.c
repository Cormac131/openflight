/* IWR6843 ball-placement detector. See l3_ball.h. */
#include <stdio.h>
#include <string.h>

#include "l3_ball.h"

static const char *const kStateNames[5] = {
    "off", "building", "waiting", "candidate", "locked"
};

static const char *const kReasonNames[L3_BALL_REASON_COUNT] = {
    "none", "no_delta", "too_wide", "unstable", "gone"
};

void l3_ball_cfg_defaults(l3_ball_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->minRatio = 1.0F;       /* the bin's static power at least doubles */
    cfg->buildUpdates = 64U;    /* ~0.4 s at one update per 2 frames of 3 ms */
    cfg->stableUpdates = 12U;   /* ~70 ms in place before READY */
    cfg->goneFraction = 0.3F;
    cfg->goneUpdates = 20U;     /* ~120 ms gone before release: longer than acquisition */
}

int32_t l3_ball_cfg_check(const l3_ball_cfg_t *cfg)
{
    if (!(cfg->minRatio > 0.0F)) {
        return -1;
    }
    if (cfg->buildUpdates == 0U || cfg->stableUpdates == 0U || cfg->goneUpdates == 0U) {
        return -1;
    }
    if (!(cfg->goneFraction > 0.0F) || cfg->goneFraction >= 1.0F) {
        return -1;
    }
    return 0;
}

void l3_ball_init(l3_ball_t *ball, const l3_ball_cfg_t *cfg)
{
    memset(ball, 0, sizeof(*ball));
    ball->cfg = *cfg;
    ball->state = cfg->enabled ? L3_BALL_STATE_BUILDING : L3_BALL_STATE_OFF;
}

static void l3_ball_restart(l3_ball_t *ball, uint32_t windowStartBin, uint32_t count)
{
    ball->updates = 0U;
    ball->windowStartBin = windowStartBin;
    ball->count = count;
    memset(ball->background, 0, sizeof(ball->background));
    memset(ball->current, 0, sizeof(ball->current));
    ball->candidateAge = 0U;
    ball->goneAge = 0U;
    ball->history = 0U;
    ball->state = L3_BALL_STATE_BUILDING;
    ball->reason = L3_BALL_REASON_NONE;
}

static float l3_ball_floor(float value)
{
    return (value > L3_BALL_POWER_MIN) ? value : L3_BALL_POWER_MIN;
}

/* (current - background) / background of one window index. */
static float l3_ball_ratioAt(const l3_ball_t *ball, uint32_t localBin)
{
    float background = l3_ball_floor(ball->background[localBin]);
    return (ball->current[localBin] - background) / background;
}

static void l3_ball_note(l3_ball_t *ball, uint8_t reason)
{
    ball->reason = reason;
    ball->reasons[reason]++;
}

/* The cluster around the strongest rise: contiguous bins whose ratio is at
 * least half of it. Sets width and the delta-weighted centroid (global,
 * sub-bin) and reports the cluster's window indices. A ball is one bin
 * wide, two when it straddles; a person or a moved chair raises a stretch
 * of bins together. */
static void l3_ball_measureCluster(l3_ball_t *ball, uint32_t bestLocal, float bestRatio,
                                   uint32_t *clusterLo, uint32_t *clusterHi)
{
    uint32_t lo = bestLocal;
    uint32_t hi = bestLocal;
    uint32_t i;
    float weight = 0.0F;
    float moment = 0.0F;

    while (lo > 0U && l3_ball_ratioAt(ball, lo - 1U) >= 0.5F * bestRatio) {
        lo--;
    }
    while (hi + 1U < ball->count && l3_ball_ratioAt(ball, hi + 1U) >= 0.5F * bestRatio) {
        hi++;
    }
    for (i = lo; i <= hi; i++) {
        float delta = ball->current[i] - ball->background[i];
        if (delta > 0.0F) {
            weight += delta;
            moment += delta * (float)(ball->windowStartBin + i);
        }
    }
    ball->width = hi - lo + 1U;
    ball->centroid = (weight > 0.0F) ? (moment / weight) : (float)(ball->windowStartBin + bestLocal);
    *clusterLo = lo;
    *clusterHi = hi;
}

/* Window index of the strongest compact new reflector, or -1 with the
 * reason noted. A rise too wide to be a ball (a player standing in the
 * lane, the hand placing the ball) is set aside, cluster and all, and the
 * next strongest rise is tried: the ball beside a person is still a ball.
 * Every pass sets aside at least the bin it examined, so the loop ends. */
static int32_t l3_ball_findCandidate(l3_ball_t *ball)
{
    uint64_t setAside = 0U;
    uint8_t sawWide = 0U;

    for (;;) {
        int32_t best = -1;
        float bestRatio = 0.0F;
        uint32_t lo;
        uint32_t hi;
        uint32_t i;

        for (i = 0U; i < ball->count; i++) {
            float ratio;

            if ((setAside & ((uint64_t)1U << i)) != 0U) {
                continue;
            }
            ratio = l3_ball_ratioAt(ball, i);
            if (ratio >= ball->cfg.minRatio && (best < 0 || ratio > bestRatio)) {
                best = (int32_t)i;
                bestRatio = ratio;
            }
        }
        if (best < 0) {
            if (sawWide) {
                /* Counted when it was set aside; the reason still names it. */
                ball->reason = L3_BALL_REASON_TOO_WIDE;
            } else {
                l3_ball_note(ball, L3_BALL_REASON_NO_DELTA);
            }
            return -1;
        }
        l3_ball_measureCluster(ball, (uint32_t)best, bestRatio, &lo, &hi);
        if (ball->width <= 2U) {
            return best;
        }
        ball->reasons[L3_BALL_REASON_TOO_WIDE]++;
        sawWide = 1U;
        for (i = lo; i <= hi; i++) {
            setAside |= (uint64_t)1U << i;
        }
    }
}

uint8_t l3_ball_update(l3_ball_t *ball, uint32_t windowStartBin, const float *power, uint32_t count)
{
    const l3_ball_cfg_t *cfg = &ball->cfg;
    uint32_t i;
    int32_t candidate;
    uint8_t seen = 0U;

    if (ball->state == L3_BALL_STATE_OFF) {
        return ball->state;
    }
    if (count > L3_BALL_MAX_BINS) {
        count = L3_BALL_MAX_BINS;
    }
    if (count == 0U) {
        return ball->state;
    }
    if (ball->windowStartBin != windowStartBin || ball->count != count) {
        /* Another window: the background no longer describes these bins. */
        l3_ball_restart(ball, windowStartBin, count);
    }
    ball->updates++;
    for (i = 0U; i < count; i++) {
        if (ball->updates == 1U) {
            ball->current[i] = power[i];
            ball->background[i] = power[i];
        } else {
            ball->current[i] += (power[i] - ball->current[i]) / (float)(1U << L3_BALL_CURRENT_SHIFT);
        }
    }

    if (ball->state == L3_BALL_STATE_BUILDING) {
        for (i = 0U; i < count; i++) {
            /* Learn fast while building: nothing is being protected yet. */
            ball->background[i] += (ball->current[i] - ball->background[i]) * 0.125F;
        }
        if (ball->updates >= cfg->buildUpdates) {
            ball->state = L3_BALL_STATE_WAITING;
        }
        ball->history <<= 1;
        return ball->state;
    }

    /* Slow background learning, except around a locked ball or a candidate:
     * a ball learned into the background while it is being confirmed would
     * shrink its own ratio, and one sitting on the tee for a minute would
     * become the background. Elsewhere a bin that has clearly risen holds a
     * new reflector rather than drift and learns at the far slower rise
     * rate, so a ball that is not yet compact and alone (under the hand
     * placing it, beside a player) survives to be locked on. */
    for (i = 0U; i < count; i++) {
        uint32_t globalBin = windowStartBin + i;
        uint32_t hold = (ball->state == L3_BALL_STATE_LOCKED) ? ball->ballBin
                        : (ball->state == L3_BALL_STATE_CANDIDATE) ? ball->candidateBin : 0U;
        uint32_t shift = L3_BALL_BACKGROUND_SHIFT;

        if (ball->state != L3_BALL_STATE_WAITING &&
            globalBin + L3_BALL_HOLD_BINS >= hold && globalBin <= hold + L3_BALL_HOLD_BINS) {
            continue;
        }
        if (l3_ball_ratioAt(ball, i) >= L3_BALL_RISE_FRACTION * cfg->minRatio) {
            shift = L3_BALL_RISE_SHIFT;
        }
        ball->background[i] += (ball->current[i] - ball->background[i]) / (float)(1U << shift);
    }

    if (ball->state == L3_BALL_STATE_LOCKED) {
        /* Still there? The ball may sit across two bins; take the better of
         * the locked bin and its neighbours. The bin itself stays frozen:
         * the club and the launched ball must not drag it. */
        uint32_t localBin = ball->ballBin - windowStartBin;
        float delta = ball->current[localBin] - ball->background[localBin];
        if (localBin > 0U) {
            float left = ball->current[localBin - 1U] - ball->background[localBin - 1U];
            delta = (left > delta) ? left : delta;
        }
        if (localBin + 1U < count) {
            float right = ball->current[localBin + 1U] - ball->background[localBin + 1U];
            delta = (right > delta) ? right : delta;
        }
        ball->ballAge++;
        if (delta > ball->ballDelta) {
            /* The fast profile is still settling right after the lock; the
             * gone test compares against the settled return. */
            ball->ballDelta = delta;
        }
        if (delta < cfg->goneFraction * ball->ballDelta) {
            ball->goneAge++;
            l3_ball_note(ball, L3_BALL_REASON_GONE);
            if (ball->goneAge >= cfg->goneUpdates) {
                ball->state = L3_BALL_STATE_WAITING;
                ball->releases++;
                ball->goneAge = 0U;
            }
        } else {
            ball->goneAge = 0U;
            ball->reason = L3_BALL_REASON_NONE;
            seen = 1U;
            {
                uint32_t lo;
                uint32_t hi;

                l3_ball_measureCluster(ball, localBin, l3_ball_ratioAt(ball, localBin), &lo, &hi);
            }
        }
        ball->history = (ball->history << 1) | seen;
        return ball->state;
    }

    candidate = l3_ball_findCandidate(ball);
    if (candidate < 0) {
        if (ball->state == L3_BALL_STATE_CANDIDATE) {
            l3_ball_note(ball, L3_BALL_REASON_UNSTABLE);
        }
        ball->state = L3_BALL_STATE_WAITING;
        ball->candidateAge = 0U;
        ball->history <<= 1;
        return ball->state;
    }
    {
        uint32_t globalBin = windowStartBin + (uint32_t)candidate;
        if (ball->state == L3_BALL_STATE_CANDIDATE &&
            globalBin + 1U >= ball->candidateBin && globalBin <= ball->candidateBin + 1U) {
            ball->candidateAge++;
        } else {
            if (ball->state == L3_BALL_STATE_CANDIDATE) {
                l3_ball_note(ball, L3_BALL_REASON_UNSTABLE);
            }
            ball->candidateAge = 1U;
        }
        ball->candidateBin = globalBin;
        ball->state = L3_BALL_STATE_CANDIDATE;
        seen = 1U;
        if (ball->candidateAge >= cfg->stableUpdates) {
            ball->state = L3_BALL_STATE_LOCKED;
            ball->ballBin = globalBin;
            ball->ballCentroid = ball->centroid;
            ball->ballBackground = l3_ball_floor(ball->background[candidate]);
            ball->ballDelta = ball->current[candidate] - ball->background[candidate];
            ball->ballAge = 0U;
            ball->goneAge = 0U;
            ball->reason = L3_BALL_REASON_NONE;
            ball->locks++;
        }
    }
    ball->history = (ball->history << 1) | seen;
    return ball->state;
}

int32_t l3_ball_locked(const l3_ball_t *ball, uint32_t *bin)
{
    if (ball->state != L3_BALL_STATE_LOCKED) {
        return 0;
    }
    *bin = ball->ballBin;
    return 1;
}

/* Live: the locked bin's current rise over its (frozen) background. */
float l3_ball_ratio(const l3_ball_t *ball)
{
    if (ball->state != L3_BALL_STATE_LOCKED) {
        return 0.0F;
    }
    return l3_ball_ratioAt(ball, ball->ballBin - ball->windowStartBin);
}

float l3_ball_persistence(const l3_ball_t *ball)
{
    uint64_t mask = ball->history & ((((uint64_t)1U) << L3_BALL_HISTORY) - 1U);
    uint32_t seen = 0U;

    while (mask != 0U) {
        seen += (uint32_t)(mask & 1U);
        mask >>= 1;
    }
    return (float)seen / (float)L3_BALL_HISTORY;
}

float l3_ball_confidence(const l3_ball_t *ball)
{
    float contrast;
    float compact;
    float stability;
    float drift;

    if (ball->state != L3_BALL_STATE_LOCKED) {
        return 0.0F;
    }
    contrast = l3_ball_ratio(ball) / L3_BALL_FULL_RATIO;
    if (contrast > 1.0F) {
        contrast = 1.0F;
    }
    if (contrast < 0.0F) {
        contrast = 0.0F;
    }
    compact = (ball->width <= 2U) ? 1.0F : (ball->width == 3U) ? 0.6F : 0.2F;
    drift = ball->centroid - ball->ballCentroid;
    if (drift < 0.0F) {
        drift = -drift;
    }
    stability = (drift >= 1.0F) ? 0.0F : (1.0F - drift);
    return contrast * compact * l3_ball_persistence(ball) * stability;
}

const char *l3_ball_state_name(uint8_t state)
{
    return (state < 5U) ? kStateNames[state] : "?";
}

const char *l3_ball_reason_name(uint8_t reason)
{
    return (reason < L3_BALL_REASON_COUNT) ? kReasonNames[reason] : "?";
}

/* "12.34" without float printf. */
static void l3_ball_fmt2(float value, char *out, uint32_t cap)
{
    unsigned whole;
    unsigned hundredths;

    if (value < 0.0F) {
        value = 0.0F;
    }
    if (value > 4.0e9F) {
        value = 4.0e9F;
    }
    whole = (unsigned)value;
    hundredths = (unsigned)((value - (float)whole) * 100.0F + 0.5F);
    if (hundredths >= 100U) {
        whole++;
        hundredths = 0U;
    }
    (void)snprintf(out, cap, "%u.%02u", whole, hundredths);
}

int32_t l3_ball_format_status(const l3_ball_t *ball, char *out, uint32_t cap)
{
    char ratioText[16];
    char confidenceText[16];
    float delta = (ball->state == L3_BALL_STATE_LOCKED) ? ball->ballDelta : 0.0F;
    float background = (ball->state == L3_BALL_STATE_LOCKED) ? ball->ballBackground : 0.0F;

    l3_ball_fmt2(l3_ball_ratio(ball), ratioText, sizeof(ratioText));
    l3_ball_fmt2(l3_ball_confidence(ball), confidenceText, sizeof(confidenceText));
    if (delta > 4.0e9F) {
        delta = 4.0e9F;
    }
    if (background > 4.0e9F) {
        background = 4.0e9F;
    }
    return snprintf(out, cap,
                    "ball state=%s follow=%u bin=%u ratio=%s confidence=%s delta=%u "
                    "background=%u age=%u locks=%u releases=%u reason=%s window=%u+%u",
                    l3_ball_state_name(ball->state), (unsigned)ball->cfg.follow,
                    (unsigned)((ball->state == L3_BALL_STATE_LOCKED) ? ball->ballBin : 0U),
                    ratioText, confidenceText, (unsigned)delta, (unsigned)background,
                    (unsigned)ball->ballAge, (unsigned)ball->locks, (unsigned)ball->releases,
                    l3_ball_reason_name(ball->reason),
                    (unsigned)ball->windowStartBin, (unsigned)ball->count);
}

int32_t l3_ball_format_debug(const l3_ball_t *ball, char *out, uint32_t cap)
{
    char centroidText[16];
    char persistText[16];
    uint32_t seen = (uint32_t)(l3_ball_persistence(ball) * (float)L3_BALL_HISTORY + 0.5F);

    l3_ball_fmt2(ball->centroid, centroidText, sizeof(centroidText));
    (void)snprintf(persistText, sizeof(persistText), "%u/%u", (unsigned)seen,
                   (unsigned)L3_BALL_HISTORY);
    return snprintf(out, cap,
                    "balldbg updates=%u candidate=%u/%u centroid=%s width=%u persistence=%s "
                    "no_delta=%u too_wide=%u unstable=%u gone=%u",
                    (unsigned)ball->updates,
                    (unsigned)((ball->state == L3_BALL_STATE_CANDIDATE) ? ball->candidateBin : 0U),
                    (unsigned)ball->candidateAge, centroidText, (unsigned)ball->width,
                    persistText,
                    (unsigned)ball->reasons[L3_BALL_REASON_NO_DELTA],
                    (unsigned)ball->reasons[L3_BALL_REASON_TOO_WIDE],
                    (unsigned)ball->reasons[L3_BALL_REASON_UNSTABLE],
                    (unsigned)ball->reasons[L3_BALL_REASON_GONE]);
}
