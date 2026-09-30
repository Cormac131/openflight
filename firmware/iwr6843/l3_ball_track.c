/* See l3_ball_track.h. */
#include <stdio.h>
#include <string.h>

#include "l3_ball_track.h"
#include "l3_text.h"

static const char *const kWhyNames[L3_BALL_TRACK_WHY_COUNT] = {
    "none", "unarmed", "nocandidate", "acquired", "confirmed", "tooslow", "toofast", "tracked",
    "coasted", "lost", "searching"
};

void l3_ball_track_cfg_defaults(l3_ball_track_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    l3_track_cfg_defaults(&cfg->core);
    cfg->core.gateBins = 6.0F;        /* a 70 m/s ball moves ~4.5 bins per 3 ms frame */
    /* A departing ball smears within a frame: its first points read 0.04-0.13
     * on the labelled swings, and at the club's 0.2 the follow-through was
     * taken instead. The second point's range rate refuses slow returns. */
    cfg->core.minConfidence = 0.05F;
    cfg->core.maxMisses = 1U;
    /* The club rules (ascending bins, at most two per bin) describe the
     * approach; the ball tracker has its own departure tests. */
    cfg->core.ascendingOnly = 0U;
    cfg->core.maxSameBinPoints = 0U;
    cfg->core.approachMaxSameBinPoints = 0U;
    cfg->core.standingFrames = 0U;
    cfg->minDepartureMps = 10.0F;     /* the slowest chip leaves faster than this */
    cfg->maxSpeedMps = 100.0F;
    cfg->originGateBins = 8.0F;       /* the first post frame is at most ~5 bins out */
    cfg->minDepartureBins = 1.0F;     /* the impact echo sits at the origin itself */
    cfg->displaceConfidence = 0.2F;   /* a clean return, as the club tracker needs */
    cfg->launchPoints = 6U;
    cfg->snr = 1.0F;                  /* the floor itself: the ball is weak and moving */
    cfg->useHypotheses = 0U;          /* decided by the recorded captures */
    cfg->skipClubClaim = 1U;
    cfg->lateRangeM = 0.6F;           /* past the floor-image flips near launch */
#if L3_BALL_HYPOTHESES
    l3_ball_hyps_cfg_defaults(&cfg->hyps);
#endif
}

void l3_ball_track_init(l3_ball_track_t *track, const l3_ball_track_cfg_t *cfg)
{
    memset(track, 0, sizeof(*track));
    track->cfg = *cfg;
    track->lastTargetIndex = L3_TRACK_NO_TARGET;
    l3_track_init(&track->core, &cfg->core);
#if L3_BALL_HYPOTHESES
    /* The hypotheses share the core's geometry: one source for both. */
    track->cfg.hyps.binWidthM = cfg->core.binWidthM;
    track->cfg.hyps.velocitySpanMps = cfg->core.velocitySpanMps;
    track->verdict.index = -1;
    l3_ball_hyps_init(&track->hyps, &track->cfg.hyps);
#endif
}

void l3_ball_track_reset(l3_ball_track_t *track)
{
    l3_track_reset(&track->core);
    track->armed = 0U;
    track->confirmed = 0U;
    track->done = 0U;
    track->why = L3_BALL_TRACK_WHY_NONE;
    track->impactTimestampUs = 0U;
    track->originBin = 0.0F;
    track->lastTargetIndex = L3_TRACK_NO_TARGET;
    memset(&track->origin, 0, sizeof(track->origin));
#if L3_BALL_HYPOTHESES
    l3_ball_hyps_init(&track->hyps, &track->cfg.hyps);
    memset(&track->verdict, 0, sizeof(track->verdict));
    track->verdict.index = -1;
#endif
}

void l3_ball_track_arm(l3_ball_track_t *track, float originBin, const l3_vec3_t *origin,
                       uint32_t impactTimestampUs)
{
    l3_ball_track_reset(track);
    track->armed = 1U;
    track->originBin = originBin;
    track->origin = *origin;
    track->impactTimestampUs = impactTimestampUs;
#if L3_BALL_HYPOTHESES
    l3_ball_hyps_arm(&track->hyps, originBin, impactTimestampUs);
#endif
}

static int32_t l3_ball_track_note(l3_ball_track_t *track, uint8_t why, int32_t appended)
{
    track->why = why;
    track->counters[why]++;
    return appended;
}

/* The second point's range rate must be a ball's; the core is reset when not. */
static int32_t l3_ball_track_confirm(l3_ball_track_t *track)
{
    l3_track_point_t newest;

    (void)l3_track_point(&track->core, track->core.count - 1U, &newest);
    if (newest.radialVelocityMps < track->cfg.minDepartureMps) {
        l3_track_reset(&track->core);
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_TOO_SLOW, 0);
    }
    if (newest.radialVelocityMps > track->cfg.maxSpeedMps) {
        l3_track_reset(&track->core);
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_TOO_FAST, 0);
    }
    track->confirmed = 1U;
    return l3_ball_track_note(track, L3_BALL_TRACK_WHY_CONFIRMED, 1);
}

int32_t l3_ball_track_seed(l3_ball_track_t *track, const l3_target_obs_t *first,
                           const l3_target_obs_t *second)
{
    const l3_target_obs_t *pair[2];
    float gateBins = track->core.cfg.gateBins;
    uint32_t k;

    if (!track->armed || track->done || track->confirmed) {
        return 0;
    }
    pair[0] = first;
    pair[1] = second;
    l3_track_reset(&track->core);
    /* Known to be the ball: neither the frame-counted gate nor the
     * acquisition's confidence may refuse the pair (as l3_ball_track_adopt). */
    track->core.cfg.gateBins = 1.0e9F;
    for (k = 0U; k < 2U; k++) {
        l3_target_obs_t point = *pair[k];

        point.confidence = 1.0F;
        if (!l3_track_update(&track->core, &point, 1U, point.frame, point.timestampUs)) {
            track->core.cfg.gateBins = gateBins;
            l3_track_reset(&track->core);
            return l3_ball_track_note(track, L3_BALL_TRACK_WHY_NO_CANDIDATE, 0);
        }
    }
    track->core.cfg.gateBins = gateBins;
    return l3_ball_track_confirm(track);
}

/* The most confident return in the origin gate at least displaceConfidence
 * and more confident than the unconfirmed first point, or -1: a smeared stray
 * taken first must not keep the ball, arriving behind it, from being offered
 * (20260916_184748). */
static int32_t l3_ball_track_displacer(const l3_ball_track_t *track,
                                       const l3_target_obs_t *targets, uint32_t n)
{
    l3_track_point_t first;
    int32_t best = -1;
    uint32_t i;

    if (!l3_track_point(&track->core, 0U, &first)) {
        return -1;
    }
    for (i = 0U; i < n; i++) {
        float beyond = targets[i].rangeBin - track->originBin;

        if (beyond < track->cfg.minDepartureBins || beyond > track->cfg.originGateBins ||
            targets[i].confidence < track->cfg.displaceConfidence ||
            targets[i].confidence <= first.confidence) {
            continue;
        }
        if (best < 0 || targets[i].confidence > targets[best].confidence) {
            best = (int32_t)i;
        }
    }
    return best;
}

/* The core's gate around its prediction for this frame. */
static int32_t l3_ball_track_inGate(const l3_club_track_t *core, const l3_target_obs_t *target,
                                    uint32_t frame)
{
    float predicted = core->lastBin + core->velocityBinsPerFrame * (float)(frame - core->lastFrame);
    float error = target->rangeBin - predicted;

    return ((error < 0.0F) ? -error : error) <= core->cfg.gateBins;
}

static int32_t l3_ball_track_step(l3_ball_track_t *track, const l3_target_obs_t *targets,
                                  uint32_t n, uint32_t frame, uint32_t timestampUs,
                                  uint32_t skipIndex)
{
    l3_target_obs_t candidates[L3_OBS_MAX_TARGETS];
    uint32_t indices[L3_OBS_MAX_TARGETS];  /* candidate -> targets index */
    uint32_t kept = 0U;
    uint32_t i;
    int32_t appended;

    memset(candidates, 0, sizeof(candidates));
    track->lastTargetIndex = L3_TRACK_NO_TARGET;
    if (!track->armed) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_UNARMED, 0);
    }
    if (track->done) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_LOST, 0);
    }
    /* A ball never comes back toward the radar: nothing behind the last point
     * (or, before acquisition, at or short of the origin) can be it. Before
     * acquisition every candidate in the departure band is offered and the
     * core takes the most confident; the ball does not reliably outrun the
     * club's follow-through at once (the hypothesis search handles that). */
    if (track->core.active && track->confirmed) {
        uint8_t otherInGate = 0U;

        for (i = 0U; i < n && kept < L3_OBS_MAX_TARGETS; i++) {
            if (targets[i].rangeBin < track->core.lastBin - 0.5F || i == skipIndex) {
                continue;
            }
            otherInGate |= (uint8_t)l3_ball_track_inGate(&track->core, &targets[i], frame);
            indices[kept] = i;
            candidates[kept++] = targets[i];
        }
        if (skipIndex < n && !otherInGate && kept < L3_OBS_MAX_TARGETS &&
            targets[skipIndex].rangeBin >= track->core.lastBin - 0.5F) {
            /* Only the club's return is in the gate: the two share a bin. */
            indices[kept] = skipIndex;
            candidates[kept++] = targets[skipIndex];
        }
    } else {
        /* Acquiring, or confirming from a single point (whose prediction is
         * the point itself): only candidates in the departure band beyond the
         * reference (the origin, then the first point) are offered, which
         * excludes the impact echo, the resting club and the follow-through
         * behind the ball; the core then takes the most confident. */
        float reference = track->core.active ? track->core.lastBin : track->originBin;
        float span = track->core.active ? track->cfg.core.gateBins : track->cfg.originGateBins;

        for (i = 0U; i < n && kept < L3_OBS_MAX_TARGETS; i++) {
            float beyond = targets[i].rangeBin - reference;

            if (beyond < track->cfg.minDepartureBins || beyond > span) {
                continue;
            }
            indices[kept] = i;
            candidates[kept++] = targets[i];
        }
    }
    if (kept == 0U && !track->core.active) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_NO_CANDIDATE, 0);
    }
    appended = l3_track_update(&track->core, candidates, kept, frame, timestampUs);
    if (!appended && !track->confirmed && track->core.count == 1U) {
        /* Nothing confirmed the first point: a confident departure replaces it. */
        int32_t displacer = l3_ball_track_displacer(track, targets, n);

        if (displacer >= 0) {
            l3_track_reset(&track->core);
            appended = l3_track_update(&track->core, &targets[displacer], 1U, frame,
                                       timestampUs);
            if (appended) {
                track->lastTargetIndex = (uint32_t)displacer;
                return l3_ball_track_note(track, L3_BALL_TRACK_WHY_ACQUIRED, 1);
            }
        }
    }
    if (appended && track->core.lastTargetIndex < kept) {
        track->lastTargetIndex = indices[track->core.lastTargetIndex];
    }
    if (!appended) {
        if (!track->core.active) {
            if (track->confirmed) {
                track->done = 1U;
                return l3_ball_track_note(track, L3_BALL_TRACK_WHY_LOST, 0);
            }
            return l3_ball_track_note(track, L3_BALL_TRACK_WHY_NO_CANDIDATE, 0);
        }
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_COASTED, 0);
    }
    if (track->core.count == 1U) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_ACQUIRED, 1);
    }
    if (!track->confirmed) {
        return l3_ball_track_confirm(track);
    }
    return l3_ball_track_note(track, L3_BALL_TRACK_WHY_TRACKED, 1);
}

#if L3_BALL_HYPOTHESES
/* Once the ball is chosen (or the search is over) the hypotheses claim no
 * target: nothing downstream estimates angles for them. */
static void l3_ball_track_quietHyps(l3_ball_track_t *track)
{
    uint32_t i;

    for (i = 0U; i < L3_BALL_HYP_MAX; i++) {
        track->hyps.hyp[i].lastTargetIndex = L3_BALL_HYP_NONE;
    }
}

/* The classified hypothesis becomes the track: its points (angles included)
 * seed the core in order, and tracking carries on from them. */
static int32_t l3_ball_track_adopt(l3_ball_track_t *track, uint32_t index)
{
    const l3_ball_hyp_t *hyp = &track->hyps.hyp[index];
    float gateBins = track->core.cfg.gateBins;
    uint32_t k;

    l3_track_reset(&track->core);
    /* The points were associated on timestamps by the hypothesis already;
     * the core's frame-counted gate must not refuse them on the way in. */
    track->core.cfg.gateBins = 1.0e9F;
    for (k = 0U; k < hyp->count; k++) {
        const l3_ball_hyp_point_t *p = &hyp->points[k];
        l3_target_obs_t seed;

        memset(&seed, 0, sizeof(seed));
        seed.frame = p->frame;
        seed.timestampUs = p->timestampUs;
        seed.peakBin = (uint8_t)(p->rangeBin + 0.5F);
        seed.rangeBin = p->rangeBin;
        seed.stat = p->stat;
        seed.peak = p->stat;
        seed.dopplerAliasMps = p->dopplerAliasMps;
        seed.coherence = 1.0F;
        seed.confidence = 1.0F;
        if (!l3_track_update(&track->core, &seed, 1U, p->frame, p->timestampUs)) {
            track->core.cfg.gateBins = gateBins;
            l3_track_reset(&track->core);
            return l3_ball_track_note(track, L3_BALL_TRACK_WHY_SEARCHING, 0);
        }
        if (p->anglesValid) {
            (void)l3_track_set_angles(&track->core, p->azimuthRad, p->elevationRad,
                                      p->anglesValid);
        }
    }
    track->core.cfg.gateBins = gateBins;
    track->confirmed = 1U;
    track->lastTargetIndex = hyp->lastTargetIndex;
    l3_ball_track_quietHyps(track);
    return l3_ball_track_note(track, L3_BALL_TRACK_WHY_CONFIRMED,
                              (hyp->lastTargetIndex != L3_BALL_HYP_NONE) ? 1 : 0);
}
#endif /* L3_BALL_HYPOTHESES */

int32_t l3_ball_track_update_joint(l3_ball_track_t *track, const l3_target_obs_t *targets,
                                   uint32_t n, uint32_t frame, uint32_t timestampUs,
                                   uint32_t clubIndex)
{
#if L3_BALL_HYPOTHESES
    uint32_t skip = track->cfg.skipClubClaim ? clubIndex : L3_TRACK_NO_TARGET;

    if (!track->cfg.useHypotheses) {
        return l3_ball_track_step(track, targets, n, frame, timestampUs, L3_TRACK_NO_TARGET);
    }
    track->lastTargetIndex = L3_TRACK_NO_TARGET;
    if (!track->armed || track->done || track->confirmed) {
        l3_ball_track_quietHyps(track);
    }
    if (!track->armed) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_UNARMED, 0);
    }
    if (track->done) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_LOST, 0);
    }
    if (track->confirmed) {
        return l3_ball_track_step(track, targets, n, frame, timestampUs, skip);
    }
    (void)l3_ball_hyps_update(&track->hyps, targets, n, frame, timestampUs, clubIndex);
    l3_ball_hyps_classify(&track->hyps, &track->verdict);
    if (track->verdict.index < 0) {
        return l3_ball_track_note(track, L3_BALL_TRACK_WHY_SEARCHING, 0);
    }
    return l3_ball_track_adopt(track, (uint32_t)track->verdict.index);
#else
    (void)clubIndex;
    return l3_ball_track_step(track, targets, n, frame, timestampUs, L3_TRACK_NO_TARGET);
#endif
}

int32_t l3_ball_track_update(l3_ball_track_t *track, const l3_target_obs_t *targets, uint32_t n,
                             uint32_t frame, uint32_t timestampUs)
{
    return l3_ball_track_update_joint(track, targets, n, frame, timestampUs, L3_TRACK_NO_TARGET);
}

uint32_t l3_ball_track_struct_bytes(void)
{
    return (uint32_t)sizeof(l3_ball_track_t);
}

int32_t l3_ball_track_set_angles(l3_ball_track_t *track, float azimuthRad, float elevationRad,
                                 uint8_t anglesValid)
{
    return l3_track_set_angles(&track->core, azimuthRad, elevationRad, anglesValid);
}

/* The first point at least lateRangeM beyond the origin; count when none. */
static uint32_t l3_ball_track_late_first(const l3_ball_track_t *track)
{
    uint32_t i;
    l3_track_point_t point;

    for (i = 0U; i < track->core.count; i++) {
        if (l3_track_point(&track->core, i, &point) &&
            (point.rangeBin - track->originBin) * track->core.cfg.binWidthM >=
                track->cfg.lateRangeM) {
            return i;
        }
    }
    return track->core.count;
}

uint32_t l3_ball_track_launch(const l3_ball_track_t *track, l3_launch_t *out)
{
    l3_delivery_t fit;
    l3_delivery_t late;
    uint32_t used;
    uint32_t first;

    memset(out, 0, sizeof(*out));
    out->lateFrom = L3_LAUNCH_NO_LATE;
    if (!track->confirmed) {
        return 0U;
    }
    used = l3_track_delivery_range(&track->core, 0U, track->cfg.launchPoints,
                                   track->cfg.launchPoints, &fit);
    if (used == 0U) {
        return 0U;
    }
    l3_launch_from_delivery(&fit, track->impactTimestampUs, out);
    /* The speed keeps the early fit; angles only from the late window. */
    out->hlaValid = 0U;
    out->vlaValid = 0U;
    out->hlaRad = 0.0F;
    out->vlaRad = 0.0F;
    first = l3_ball_track_late_first(track);
    if (first < track->core.count &&
        l3_track_delivery_range(&track->core, first, track->cfg.launchPoints,
                                track->cfg.launchPoints, &late) != 0U) {
        if (late.pathValid) {
            out->hlaRad = late.pathRad;
            out->hlaValid = 1U;
        }
        if (late.attackValid) {
            out->vlaRad = late.attackRad;
            out->vlaValid = 1U;
        }
        if (out->hlaValid || out->vlaValid) {
            out->lateFrom = (uint8_t)((first > 0xFEU) ? 0xFEU : first);
        }
    }
    return used;
}

const char *l3_ball_track_why_name(uint8_t why)
{
    return (why < L3_BALL_TRACK_WHY_COUNT) ? kWhyNames[why] : "?";
}

int32_t l3_ball_track_format_status(const l3_ball_track_t *track, char *out, uint32_t cap)
{
    char originText[16];
    char binText[16];

    l3_text_fixed2(track->originBin, originText, sizeof(originText));
    l3_text_fixed2(track->core.lastBin, binText, sizeof(binText));
    return snprintf(out, cap,
                    "balltrack armed=%u confirmed=%u done=%u why=%s count=%u origin=%s bin=%s "
                    "impact=%u acq=%u slow=%u fast=%u lost=%u",
                    (unsigned)track->armed, (unsigned)track->confirmed, (unsigned)track->done,
                    l3_ball_track_why_name(track->why), (unsigned)track->core.count, originText,
                    binText, (unsigned)track->impactTimestampUs,
                    (unsigned)track->counters[L3_BALL_TRACK_WHY_ACQUIRED],
                    (unsigned)track->counters[L3_BALL_TRACK_WHY_TOO_SLOW],
                    (unsigned)track->counters[L3_BALL_TRACK_WHY_TOO_FAST],
                    (unsigned)track->counters[L3_BALL_TRACK_WHY_LOST]);
}

