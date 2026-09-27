/* See l3_retain.h. */
#include <stdio.h>
#include <string.h>

#include "l3_retain.h"
#include "l3_shot.h"

void l3_retain_cfg_defaults(l3_retain_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->enabled = 1U;
    cfg->approachBins = 12U;        /* about 0.56 m of club-to-ball at 46.9 mm bins */
    cfg->approachMarginBins = 3U;
    cfg->impactBiasBins = 4U;       /* the club arrives from the near side */
    cfg->ballSearchLeadBins = 2U;   /* the impact echo sits at the origin */
    cfg->ballFollowLeadBins = 4U;   /* the flight moves away: more window ahead */
    cfg->spinFrames = 4U;
}

int32_t l3_retain_cfg_check(const l3_retain_cfg_t *cfg)
{
    if (cfg->approachBins == 0U || cfg->approachBins > 64U) {
        return -1;
    }
    if (cfg->approachMarginBins > 32U || cfg->impactBiasBins > 32U ||
        cfg->ballSearchLeadBins > 32U || cfg->ballFollowLeadBins > 32U) {
        return -1;
    }
    return 0;
}

float l3_retain_predict(float lastBin, float velocityBinsPerFrame)
{
    return lastBin + velocityBinsPerFrame;
}

static uint8_t l3_retain_clip(int32_t start, uint32_t processStart, uint32_t processBins,
                              uint32_t retainBins)
{
    int32_t first = (int32_t)processStart;
    int32_t last = (int32_t)processStart + (int32_t)processBins - (int32_t)retainBins;

    if (last < first) {
        last = first;
    }
    if (start < first) {
        start = first;
    }
    if (start > last) {
        start = last;
    }
    return (uint8_t)start;
}

/* A window of `retainBins` centred on `centre` (bins): the start is
 * floor(centre - bins / 2 + 1), so an even width puts one more bin beyond
 * the centre than before it (targets of interest move away from the radar). */
static int32_t l3_retain_centred(float centre, uint32_t retainBins)
{
    float start = centre - 0.5F * (float)retainBins + 1.0F;
    int32_t whole = (int32_t)start;

    if ((float)whole > start) {
        whole--;
    }
    return whole;
}

static float l3_retain_min(float a, float b)
{
    return (a < b) ? a : b;
}

static float l3_retain_max(float a, float b)
{
    return (a > b) ? a : b;
}

void l3_retain_window(const l3_retain_cfg_t *cfg, const l3_retain_state_t *state,
                      uint32_t processStart, uint32_t processBins, uint32_t retainBins,
                      l3_retain_window_t *out)
{
    int32_t start;

    memset(out, 0, sizeof(*out));
    if (retainBins == 0U) {
        retainBins = processBins;
    }
    if (retainBins > processBins) {
        retainBins = processBins;
    }
    out->bins = (uint8_t)retainBins;
    if (!cfg->enabled) {
        out->start = l3_retain_clip((int32_t)processStart +
                                        ((int32_t)processBins - (int32_t)retainBins) / 2,
                                    processStart, processBins, retainBins);
        out->priority = L3_RETAIN_LOW;
        out->why = L3_RETAIN_WHY_CENTRED;
        return;
    }
    if (state->postFrame) {
        if (state->ballTrackConfirmed) {
            /* The flight moves away from the radar: put most of the window
             * ahead of the prediction. */
            start = (int32_t)(state->ballTrackBin + 0.5F) - (int32_t)cfg->ballFollowLeadBins;
            out->priority = L3_RETAIN_BALL;
            out->why = L3_RETAIN_WHY_BALL_FOLLOW;
        } else if (state->shotState == L3_SHOT_IMPACT ||
                   state->shotState == L3_SHOT_CLUB_TRACK ||
                   state->shotState == L3_SHOT_CLUB_ACQUIRE) {
            /* The impact frames: the ball and the club arriving short of it. */
            start = l3_retain_centred(state->ballBin, retainBins) - (int32_t)cfg->impactBiasBins;
            out->priority = L3_RETAIN_IMPACT;
            out->why = L3_RETAIN_WHY_IMPACT;
        } else {
            /* Searching for the departing ball: from the origin outward. */
            start = (int32_t)(state->ballBin + 0.5F) - (int32_t)cfg->ballSearchLeadBins;
            out->priority = L3_RETAIN_IMPACT;
            out->why = L3_RETAIN_WHY_BALL_SEARCH;
        }
        if (state->postIndex < cfg->spinFrames && out->priority != L3_RETAIN_BALL) {
            out->priority = L3_RETAIN_SPIN;
        }
        out->start = l3_retain_clip(start, processStart, processBins, retainBins);
        return;
    }
    if (state->clubActive &&
        (state->shotState == L3_SHOT_CLUB_ACQUIRE || state->shotState == L3_SHOT_CLUB_TRACK ||
         state->shotState == L3_SHOT_READY || state->shotState == L3_SHOT_WAITING_FOR_BALL)) {
        float gap = state->ballBin - state->clubBin;

        if (gap < 0.0F) {
            gap = -gap;
        }
        if (gap <= (float)cfg->approachBins) {
            float low = l3_retain_min(state->clubBin, state->ballBin) - (float)cfg->approachMarginBins;
            float high = l3_retain_max(state->clubBin, state->ballBin) + (float)cfg->approachMarginBins;

            start = l3_retain_centred(0.5F * (low + high), retainBins);
            out->priority = L3_RETAIN_IMPACT;
            out->why = L3_RETAIN_WHY_APPROACH;
        } else {
            start = l3_retain_centred(state->clubBin, retainBins);
            out->priority = L3_RETAIN_TRACK;
            out->why = L3_RETAIN_WHY_CLUB;
        }
        out->start = l3_retain_clip(start, processStart, processBins, retainBins);
        return;
    }
    start = l3_retain_centred(state->ballBin, retainBins);
    out->priority = L3_RETAIN_LOW;
    out->why = state->ballLocked ? L3_RETAIN_WHY_BALL : L3_RETAIN_WHY_TEE;
    out->start = l3_retain_clip(start, processStart, processBins, retainBins);
}

void l3_retain_roi(uint32_t processStart, uint32_t processBins, const l3_retain_window_t *window,
                   l3_roi_t *roi)
{
    roi->processStart = (uint8_t)processStart;
    roi->processBins = (uint8_t)processBins;
    roi->retainStart = window->start;
    roi->retainBins = window->bins;
}

int32_t l3_retain_budget(const l3_retain_request_t *request, l3_retain_budget_t *out)
{
    uint32_t preFrameBytes = request->bytesPerBin * request->preBins;
    uint32_t impactFrameBytes = request->bytesPerBin * request->impactBins;
    uint32_t ballFrameBytes = request->bytesPerBin * request->ballBins;
    uint32_t impactBytes = impactFrameBytes * request->impactFrames;
    uint32_t pre = request->preFrames;
    uint32_t ball = request->ballFrames;
    uint32_t total;

    memset(out, 0, sizeof(*out));
    if (request->bytesPerBin == 0U || request->preBins == 0U || request->ballBins == 0U ||
        request->preFrames == 0U || request->ballFrames == 0U ||
        (request->impactFrames > 0U && request->impactBins == 0U) || request->maxFrames == 0U) {
        return -1;
    }
    /* The mandatory core: every impact frame, one ball frame, one pre frame. */
    if (impactBytes + ballFrameBytes + preFrameBytes > request->capacityBytes ||
        (uint32_t)request->impactFrames + 2U > request->maxFrames) {
        return -1;
    }
    /* Trim in priority order: the last ball frames go before the oldest pre
     * frames, because the flight's tail is worth less than the last frames
     * of the downswing... but the FIRST ball frames are worth more than any
     * pre frame, which the mandatory core above already protects. */
    for (;;) {
        total = impactBytes + ball * ballFrameBytes + pre * preFrameBytes;
        if (total <= request->capacityBytes &&
            (uint32_t)request->impactFrames + ball + pre <= request->maxFrames) {
            break;
        }
        if (pre > 1U) {
            /* The oldest club history is the lowest value: cut pre first,
             * down to a floor that keeps a usable approach. */
            if (pre > ball || ball == 1U) {
                pre--;
                out->cutPre++;
                continue;
            }
        }
        if (ball > 1U) {
            ball--;
            out->cutBall++;
            continue;
        }
        pre--;
        out->cutPre++;
    }
    out->preFrames = (uint8_t)pre;
    out->impactFrames = request->impactFrames;
    out->ballFrames = (uint8_t)ball;
    out->usedBytes = total;
    out->freeBytes = request->capacityBytes - total;
    return 0;
}

const char *l3_retain_priority_name(uint8_t priority)
{
    static const char *const names[L3_RETAIN_PRIORITY_COUNT] = {
        "low", "track", "ball", "impact", "spin"
    };

    return (priority < L3_RETAIN_PRIORITY_COUNT) ? names[priority] : "?";
}

const char *l3_retain_why_name(uint8_t why)
{
    static const char *const names[L3_RETAIN_WHY_COUNT] = {
        "centred", "tee", "ball", "club", "approach", "impact", "ballsearch", "ballfollow"
    };

    return (why < L3_RETAIN_WHY_COUNT) ? names[why] : "?";
}

int32_t l3_retain_format(const l3_retain_window_t *window, char *out, uint32_t cap)
{
    return snprintf(out, cap, "retain start=%u bins=%u prio=%s why=%s", (unsigned)window->start,
                    (unsigned)window->bins, l3_retain_priority_name(window->priority),
                    l3_retain_why_name(window->why));
}

int32_t l3_retain_format_budget(const l3_retain_budget_t *budget, char *out, uint32_t cap)
{
    return snprintf(out, cap, "budget pre=%u impact=%u ball=%u cut=%u/%u used=%u free=%u",
                    (unsigned)budget->preFrames, (unsigned)budget->impactFrames,
                    (unsigned)budget->ballFrames, (unsigned)budget->cutPre,
                    (unsigned)budget->cutBall, (unsigned)budget->usedBytes,
                    (unsigned)budget->freeBytes);
}

int32_t l3_frame_desc_format(const l3_frame_desc_t *desc, char *out, uint32_t cap)
{
    return snprintf(out, cap, "frame %u t=%uus bins %u+%u of %u+%u state=%s prio=%s why=%s post=%u",
                    (unsigned)desc->frame, (unsigned)desc->timestampUs,
                    (unsigned)desc->globalBinStart, (unsigned)desc->binCount,
                    (unsigned)desc->processStart, (unsigned)desc->processBins,
                    l3_shot_state_name(desc->shotState), l3_retain_priority_name(desc->priority),
                    l3_retain_why_name(desc->why), (unsigned)desc->isPost);
}
