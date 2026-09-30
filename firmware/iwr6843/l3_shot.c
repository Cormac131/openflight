/* See l3_shot.h. */
#include <stdio.h>
#include <string.h>

#include "l3_shot.h"
#include "l3_text.h"

static const char *const kStateNames[L3_SHOT_STATE_COUNT] = {
    "waiting_for_ball", "ready", "club_acquire", "club_track", "impact", "ball_track", "solve",
    "result"
};

void l3_shot_cfg_defaults(l3_shot_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->requireBall = 0U;        /* the tee stands in until the detector is proven */
    cfg->ballTrackFrames = 16U;   /* the default post movie */
}

static void l3_shot_enter(l3_shot_t *shot, uint8_t state, uint32_t frame)
{
    if (state == shot->state) {
        return;
    }
    shot->previous = shot->state;
    shot->state = state;
    shot->enteredFrame = frame;
    shot->transitions++;
    shot->entries[state]++;
}

void l3_shot_init(l3_shot_t *shot, const l3_shot_cfg_t *cfg)
{
    memset(shot, 0, sizeof(*shot));
    shot->cfg = *cfg;
    shot->state = L3_SHOT_WAITING_FOR_BALL;
    shot->entries[L3_SHOT_WAITING_FOR_BALL] = 1U;
}

void l3_shot_rearm(l3_shot_t *shot)
{
    l3_shot_cfg_t cfg = shot->cfg;
    uint32_t entries[L3_SHOT_STATE_COUNT];
    uint32_t transitions = shot->transitions;

    memcpy(entries, shot->entries, sizeof(entries));
    l3_shot_init(shot, &cfg);
    memcpy(shot->entries, entries, sizeof(entries));
    shot->entries[L3_SHOT_WAITING_FOR_BALL]++;
    shot->transitions = transitions + 1U;
}

static void l3_shot_freeze(l3_shot_t *shot, const l3_shot_input_t *in, uint32_t frame)
{
    uint32_t i;

    shot->impactSource = (uint8_t)((in->geometricFired ? L3_SHOT_IMPACT_GEOMETRY : 0U) |
                                   (in->rangeFired ? L3_SHOT_IMPACT_RANGE : 0U));
    shot->impactFrame = frame;
    shot->impactTimestampUs = in->impactTimestampUs;
    shot->ballOrigin = in->ballPosition;
    if (in->delivery != NULL) {
        shot->delivery = *in->delivery;
    }
    shot->clubPoints = 0U;
    if (in->club != NULL) {
        for (i = 0U; i < L3_TRACK_POINTS; i++) {
            if (!l3_track_point(in->club, i, &shot->clubTrajectory[i])) {
                break;
            }
            shot->clubPoints++;
        }
    }
}

uint8_t l3_shot_update(l3_shot_t *shot, const l3_shot_input_t *in, uint32_t frame)
{
    uint8_t ballReady = (uint8_t)(in->ballLocked || !shot->cfg.requireBall);
    uint8_t fired = (uint8_t)(in->geometricFired || in->rangeFired);

    switch (shot->state) {
    case L3_SHOT_WAITING_FOR_BALL:
        if (ballReady) {
            l3_shot_enter(shot, L3_SHOT_READY, frame);
        }
        break;
    case L3_SHOT_READY:
    case L3_SHOT_CLUB_ACQUIRE:
    case L3_SHOT_CLUB_TRACK:
        if (fired) {
            l3_shot_freeze(shot, in, frame);
            shot->postFrames = 0U;
            l3_shot_enter(shot, L3_SHOT_IMPACT, frame);
        } else if (!ballReady) {
            l3_shot_enter(shot, L3_SHOT_WAITING_FOR_BALL, frame);
        } else if (!in->clubActive) {
            l3_shot_enter(shot, L3_SHOT_READY, frame);
        } else if (in->clubPoints >= 2U) {
            l3_shot_enter(shot, L3_SHOT_CLUB_TRACK, frame);
        } else {
            l3_shot_enter(shot, L3_SHOT_CLUB_ACQUIRE, frame);
        }
        break;
    case L3_SHOT_IMPACT:
        if (in->postFrame) {
            shot->postFrames++;
            l3_shot_enter(shot, L3_SHOT_BALL_TRACK, frame);
        }
        break;
    case L3_SHOT_BALL_TRACK:
        if (in->postFrame) {
            shot->postFrames++;
        }
        if (in->ballTrackDone || shot->postFrames >= shot->cfg.ballTrackFrames) {
            l3_shot_enter(shot, L3_SHOT_SOLVE, frame);
        }
        break;
    case L3_SHOT_SOLVE:
        if (in->solved) {
            l3_shot_enter(shot, L3_SHOT_RESULT, frame);
        }
        break;
    case L3_SHOT_RESULT:
    default:
        break;
    }
    return shot->state;
}

int32_t l3_shot_wants_departing(const l3_shot_t *shot)
{
    return (shot->state >= L3_SHOT_IMPACT) ? 1 : 0;
}

uint8_t l3_shot_fire_sources(uint8_t geometryArmed, int32_t geometricFired, int32_t rangeFired)
{
    uint8_t sources = rangeFired ? L3_SHOT_IMPACT_RANGE : 0U;

    if (geometryArmed && geometricFired) {
        sources |= L3_SHOT_IMPACT_GEOMETRY;
    }
    return sources;
}

const char *l3_shot_state_name(uint8_t state)
{
    return (state < L3_SHOT_STATE_COUNT) ? kStateNames[state] : "?";
}

const char *l3_shot_source_name(uint8_t source, char *buf, uint32_t cap)
{
    static const char *const kLegacy[4] = { "none", "gate", "geometry", "both" };
    static const char *const kBits[3] = { "gate", "geometry", "range" };
    uint32_t used = 0U;
    uint32_t i;

    if (source < 4U) {
        return kLegacy[source];
    }
    buf[0] = '\0';
    for (i = 0U; i < 3U; i++) {
        if (source & (1U << i)) {
            int32_t n = snprintf(buf + used, cap - used, "%s%s", used ? "+" : "", kBits[i]);

            if (n < 0 || (uint32_t)n >= cap - used) {
                break;
            }
            used += (uint32_t)n;
        }
    }
    return buf;
}

int32_t l3_shot_format(const l3_shot_t *shot, char *out, uint32_t cap)
{
    char xText[16];
    char yText[16];
    char zText[16];
    char impactText[16];
    char sourceText[24];

    l3_text_fixed2(shot->ballOrigin.x, xText, sizeof(xText));
    l3_text_fixed2(shot->ballOrigin.y, yText, sizeof(yText));
    l3_text_fixed2(shot->ballOrigin.z, zText, sizeof(zText));
    if (shot->state >= L3_SHOT_IMPACT) {
        (void)snprintf(impactText, sizeof(impactText), "%u", (unsigned)shot->impactTimestampUs);
    } else {
        (void)snprintf(impactText, sizeof(impactText), "-");
    }
    return snprintf(out, cap,
                    "shot state=%s since=%u impact=%s source=%s origin=%s,%s,%s club=%u post=%u "
                    "transitions=%u",
                    l3_shot_state_name(shot->state), (unsigned)shot->enteredFrame, impactText,
                    l3_shot_source_name(shot->impactSource, sourceText, sizeof(sourceText)),
                    xText, yText, zText, (unsigned)shot->clubPoints, (unsigned)shot->postFrames,
                    (unsigned)shot->transitions);
}
