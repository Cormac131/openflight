/* IWR6843 joint club/ball path search — implementation.
 *
 * Club and ball are independent beams. Club is scored first; surviving club
 * hypotheses claim their targets, and the ball beam skips those indices.
 * Path state (lastBin, lastTimeUs) is recovered by chain-walking the window.
 */
#include <math.h>
#include <string.h>

#include "l3_joint_search.h"

#define L3_JOINT_ILLEGAL 1.0e30F

/* --------------------------------------------------------------------------
 * Small helpers
 * -------------------------------------------------------------------------- */

static float l3_joint_dt_s(uint32_t oldUs, uint32_t newUs)
{
    int32_t diff = (int32_t)(newUs - oldUs);
    return (diff > 0) ? (float)diff * 1.0e-6F : 0.0F;
}

static float l3_joint_cap(float v, float cap)
{
    if (v >  cap) return  cap;
    if (v < -cap) return -cap;
    return v;
}

static float l3_joint_resolve_speed(float aliasedMps, float predictedMps, float span)
{
    float diff, n;
    if (span <= 0.0F) return aliasedMps;
    diff = aliasedMps - predictedMps;
    n    = floorf(diff / span + 0.5F);
    return aliasedMps - n * span;
}

static uint32_t l3_joint_slot_at(uint32_t head, uint32_t ago)
{
    return (head + L3_JOINT_WINDOW - ago) % L3_JOINT_WINDOW;
}

/* Keep the K lowest scores. Illegal / non-finite scores are dropped. */
#define POOL_TRY(pool, poolCount, cap, nd, scoreField) \
    do { \
        float _s = (nd).scoreField; \
        if (!isfinite(_s) || _s >= L3_JOINT_ILLEGAL * 0.5F) { \
            js->counters[L3_JOINT_CNT_NONFINITE] += !isfinite(_s); \
            break; \
        } \
        if ((poolCount) < (cap)) { (pool)[(poolCount)++] = (nd); break; } \
        uint32_t wi = 0U; \
        for (uint32_t _i = 1U; _i < (poolCount); _i++) \
            if ((pool)[_i].scoreField > (pool)[wi].scoreField) wi = _i; \
        if (_s < (pool)[wi].scoreField) (pool)[wi] = (nd); \
    } while (0)

/* --------------------------------------------------------------------------
 * Chain walking
 * -------------------------------------------------------------------------- */

static int32_t l3_joint_find_last_club(
    const l3_joint_t *js,
    uint32_t parentSlot, uint32_t parentNodeIdx,
    uint32_t *outSlot, uint8_t *outTargetIdx)
{
    uint32_t slot = parentSlot;
    uint32_t nidx = parentNodeIdx;
    for (uint32_t d = 0U; d < L3_JOINT_WINDOW; d++) {
        const l3_joint_club_node_t *nd = &js->clubLinks[slot].nodes[nidx];
        if (nd->target != L3_JOINT_NONE) {
            *outSlot      = slot;
            *outTargetIdx = nd->target;
            return 1;
        }
        if (nd->parent == L3_JOINT_NONE) break;
        nidx = nd->parent;
        slot = (slot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;
    }
    return 0;
}

static int32_t l3_joint_find_last_ball(
    const l3_joint_t *js,
    uint32_t parentSlot, uint32_t parentNodeIdx,
    uint32_t *outSlot, uint8_t *outTargetIdx)
{
    uint32_t slot = parentSlot;
    uint32_t nidx = parentNodeIdx;
    for (uint32_t d = 0U; d < L3_JOINT_WINDOW; d++) {
        const l3_joint_ball_node_t *nd = &js->ballLinks[slot].nodes[nidx];
        if (nd->target != L3_JOINT_NONE) {
            *outSlot      = slot;
            *outTargetIdx = nd->target;
            return 1;
        }
        if (nd->parent == L3_JOINT_NONE) break;
        nidx = nd->parent;
        slot = (slot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;
    }
    return 0;
}

/* --------------------------------------------------------------------------
 * Score one club / ball extension
 * -------------------------------------------------------------------------- */

static float l3_joint_score_club(
    l3_joint_t *js,
    uint32_t parentSlot, uint32_t parentNodeIdx,
    uint8_t clubIdx,
    const l3_joint_frame_t *headFr,
    float dtS,
    l3_joint_club_node_t *out)
{
    const l3_joint_cfg_t *cfg = &js->cfg;
    const l3_joint_club_node_t *par = &js->clubLinks[parentSlot].nodes[parentNodeIdx];
    float score = par->score;

    out->parent  = (uint8_t)parentNodeIdx;
    out->target  = clubIdx;
    out->misses  = par->misses;
    out->state   = par->state;
    out->speedMps = par->speedMps;

    if (clubIdx != L3_JOINT_NONE) {
        const l3_joint_frame_target_t *ct = &headFr->targets[clubIdx];
        float speed = l3_joint_resolve_speed(ct->dopplerAliasMps,
                                              out->speedMps,
                                              cfg->velocitySpanMps);
        if (out->state == L3_JOINT_ACTIVE) {
            uint32_t lastSlot; uint8_t lastTgt;
            if (l3_joint_find_last_club(js, parentSlot, parentNodeIdx,
                                         &lastSlot, &lastTgt)) {
                float lastBin = js->frames[lastSlot].targets[lastTgt].rangeBin;
                float sigma   = cfg->rangeSigmaBins
                              + (float)par->misses * cfg->coastSigmaGrowBins;
                float rangePred = lastBin
                    + (out->speedMps + speed) * 0.5F * dtS / cfg->binWidthM;
                float re = (ct->rangeBin - rangePred) / sigma;
                score += l3_joint_cap(re * re, cfg->termCap);
                if (dtS > 0.0F) {
                    float accel = (speed - out->speedMps) / dtS;
                    float decel = -cfg->clubDecelMps2;
                    float acc   =  cfg->clubAccelMps2;
                    float aErr  = (accel < decel) ? (accel - decel) :
                                  (accel > acc)   ? (accel - acc)   : 0.0F;
                    score += l3_joint_cap(aErr * aErr * 0.1F, cfg->termCap);
                }
            }
        }
        if (out->state == L3_JOINT_UNSTARTED) {
            out->state = L3_JOINT_ACTIVE;
        }
        out->speedMps = speed;
        out->misses   = 0U;
    } else if (out->state == L3_JOINT_ACTIVE) {
        out->misses++;
        score += cfg->missCost;
        if (out->misses > cfg->clubMaxMisses) {
            out->state = L3_JOINT_ENDED;
        }
    }

    out->score = score;
    js->counters[L3_JOINT_CNT_PAIRINGS]++;
    return score;
}

static float l3_joint_score_ball(
    l3_joint_t *js,
    uint32_t parentSlot, uint32_t parentNodeIdx,
    uint8_t ballIdx,
    const l3_joint_frame_t *headFr,
    float dtS,
    l3_joint_ball_node_t *out)
{
    const l3_joint_cfg_t *cfg = &js->cfg;
    const l3_joint_ball_node_t *par = &js->ballLinks[parentSlot].nodes[parentNodeIdx];
    float score = par->score;

    out->parent     = (uint8_t)parentNodeIdx;
    out->target     = ballIdx;
    out->misses     = par->misses;
    out->state      = par->state;
    out->speedKnown = par->speedKnown;
    out->hits       = par->hits;
    out->speedMps   = par->speedMps;

    if (ballIdx != L3_JOINT_NONE) {
        const l3_joint_frame_target_t *bt = &headFr->targets[ballIdx];

        if (out->state == L3_JOINT_UNSTARTED) {
            float originBin = js->seed.rangeBin;
            float timeFromGateS = (headFr->timestampUs > js->gateTimestampUs)
                ? (float)(int32_t)(headFr->timestampUs - js->gateTimestampUs) * 1.0e-6F
                : 0.0F;
            float maxBeyond = cfg->startBeyondBins
                            + cfg->ballMaxSpeedMps / cfg->binWidthM * timeFromGateS;
            if (bt->rangeBin < originBin - cfg->startBehindBins ||
                bt->rangeBin > originBin + maxBeyond) {
                out->score = L3_JOINT_ILLEGAL;
                return L3_JOINT_ILLEGAL;
            }
            out->state      = L3_JOINT_ACTIVE;
            out->speedMps   = 0.0F;
            out->speedKnown = 0U;
            out->misses     = 0U;
            out->hits       = 1U;
        } else if (out->state == L3_JOINT_ACTIVE) {
            float speed;
            if (!out->speedKnown) {
                uint32_t lastSlot; uint8_t lastTgt;
                if (l3_joint_find_last_ball(js, parentSlot, parentNodeIdx,
                                             &lastSlot, &lastTgt)) {
                    float lastBin = js->frames[lastSlot].targets[lastTgt].rangeBin;
                    float lastTs  = (float)(int32_t)js->frames[lastSlot].timestampUs * 1.0e-6F;
                    float curTs   = (float)(int32_t)headFr->timestampUs * 1.0e-6F;
                    float elapsed = curTs - lastTs;
                    speed = (elapsed > 0.0F)
                          ? (bt->rangeBin - lastBin) * cfg->binWidthM / elapsed
                          : 0.0F;
                } else {
                    speed = 0.0F;
                }
                if (speed < cfg->ballMinSpeedMps || speed > cfg->ballMaxSpeedMps) {
                    out->score = L3_JOINT_ILLEGAL;
                    return L3_JOINT_ILLEGAL;
                }
                out->speedKnown = 1U;
            } else {
                speed = l3_joint_resolve_speed(bt->dopplerAliasMps,
                                               out->speedMps,
                                               cfg->velocitySpanMps);
            }
            uint32_t lastSlot; uint8_t lastTgt;
            if (par->speedKnown &&
                l3_joint_find_last_ball(js, parentSlot, parentNodeIdx,
                                         &lastSlot, &lastTgt)) {
                float lastBin = js->frames[lastSlot].targets[lastTgt].rangeBin;
                float sigma   = cfg->rangeSigmaBins
                              + (float)par->misses * cfg->coastSigmaGrowBins;
                float rangePred = lastBin
                    + (out->speedMps + speed) * 0.5F * dtS / cfg->binWidthM;
                float re = (bt->rangeBin - rangePred) / sigma;
                score += l3_joint_cap(re * re, cfg->termCap);
                if (dtS > 0.0F) {
                    float accel = (speed - out->speedMps) / dtS;
                    float maxA  = cfg->ballAccelMps2;
                    float aErr  = (accel < -maxA) ? (accel + maxA) :
                                  (accel >  maxA) ? (accel - maxA) : 0.0F;
                    score += l3_joint_cap(aErr * aErr * 0.1F, cfg->termCap);
                }
            }
            out->speedMps = speed;
            out->misses   = 0U;
            if (out->hits < 255U) out->hits++;
        }
    } else if (out->state == L3_JOINT_ACTIVE) {
        out->misses++;
        score += cfg->missCost;
        if (out->misses >= cfg->ballMaxMisses) {
            out->state = L3_JOINT_ENDED;
        }
    }

    out->score = score;
    js->counters[L3_JOINT_CNT_PAIRINGS]++;
    return score;
}

/* --------------------------------------------------------------------------
 * Ancestor helpers, write-out
 * -------------------------------------------------------------------------- */

static const l3_joint_ball_node_t *l3_joint_ball_ancestor_node(
    const l3_joint_t *js, uint32_t headSlot, uint32_t nodeIdx, uint32_t depth)
{
    uint32_t slot = headSlot;
    uint32_t idx  = nodeIdx;
    for (uint32_t d = 0U; d < depth; d++) {
        uint8_t par = js->ballLinks[slot].nodes[idx].parent;
        if (par == L3_JOINT_NONE) return &js->ballLinks[slot].nodes[idx];
        idx  = par;
        slot = (slot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;
    }
    return &js->ballLinks[slot].nodes[idx];
}

static const l3_joint_club_node_t *l3_joint_club_ancestor_node(
    const l3_joint_t *js, uint32_t headSlot, uint32_t nodeIdx, uint32_t depth)
{
    uint32_t slot = headSlot;
    uint32_t idx  = nodeIdx;
    for (uint32_t d = 0U; d < depth; d++) {
        uint8_t par = js->clubLinks[slot].nodes[idx].parent;
        if (par == L3_JOINT_NONE) return &js->clubLinks[slot].nodes[idx];
        idx  = par;
        slot = (slot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;
    }
    return &js->clubLinks[slot].nodes[idx];
}

static uint8_t l3_joint_club_ancestor(
    const l3_joint_t *js, uint32_t headSlot, uint32_t nodeIdx, uint32_t depth)
{
    return l3_joint_club_ancestor_node(js, headSlot, nodeIdx, depth)->target;
}

static uint8_t l3_joint_ball_ancestor(
    const l3_joint_t *js, uint32_t headSlot, uint32_t nodeIdx, uint32_t depth)
{
    return l3_joint_ball_ancestor_node(js, headSlot, nodeIdx, depth)->target;
}

static int32_t l3_joint_club_agreed(const l3_joint_t *js, uint32_t headSlot, uint32_t depth)
{
    const l3_joint_club_link_t *head = &js->clubLinks[headSlot];
    if (head->count == 0U) return 0;
    uint8_t rc = l3_joint_club_ancestor(js, headSlot, 0U, depth);
    for (uint32_t i = 1U; i < head->count; i++) {
        if (l3_joint_club_ancestor(js, headSlot, i, depth) != rc) return 0;
    }
    return 1;
}

static int32_t l3_joint_ball_agreed(const l3_joint_t *js, uint32_t headSlot, uint32_t depth)
{
    const l3_joint_ball_link_t *head = &js->ballLinks[headSlot];
    if (head->count == 0U) return 0;
    uint8_t rb = l3_joint_ball_ancestor(js, headSlot, 0U, depth);
    for (uint32_t i = 1U; i < head->count; i++) {
        if (l3_joint_ball_ancestor(js, headSlot, i, depth) != rb) return 0;
    }
    return 1;
}

static uint32_t l3_joint_best_club(const l3_joint_club_link_t *lnk)
{
    uint32_t best = 0U;
    for (uint32_t i = 1U; i < lnk->count; i++) {
        if (lnk->nodes[i].score < lnk->nodes[best].score) best = i;
    }
    return best;
}

static int32_t l3_joint_ball_rank(const l3_joint_ball_node_t *nd)
{
    if (nd->state != L3_JOINT_UNSTARTED && nd->speedKnown) return 0;
    if (nd->state != L3_JOINT_UNSTARTED) return 1;
    return 2;
}

static uint32_t l3_joint_best_ball(const l3_joint_ball_link_t *lnk)
{
    if (lnk->count == 0U) return 0U;
    uint32_t best = 0U;
    int32_t bestRank = l3_joint_ball_rank(&lnk->nodes[0]);
    float bestScore = lnk->nodes[0].score;
    for (uint32_t i = 1U; i < lnk->count; i++) {
        int32_t rank = l3_joint_ball_rank(&lnk->nodes[i]);
        float score = lnk->nodes[i].score;
        if (rank < bestRank ||
            (rank == bestRank && lnk->nodes[i].hits > lnk->nodes[best].hits) ||
            (rank == bestRank && lnk->nodes[i].hits == lnk->nodes[best].hits &&
             score < bestScore)) {
            best = i;
            bestRank = rank;
            bestScore = score;
        }
    }
    return best;
}

static int32_t l3_joint_ball_better(const l3_joint_ball_node_t *a,
                                    const l3_joint_ball_node_t *b)
{
    int32_t ra = l3_joint_ball_rank(a);
    int32_t rb = l3_joint_ball_rank(b);
    if (ra != rb) return ra < rb;
    if (a->hits != b->hits) return a->hits > b->hits;
    return a->score < b->score;
}

static void l3_joint_ball_pool_try(l3_joint_t *js,
                                  l3_joint_ball_node_t *pool, uint32_t *count,
                                  l3_joint_ball_node_t nd)
{
    if (!isfinite(nd.score) || nd.score >= L3_JOINT_ILLEGAL * 0.5F) {
        js->counters[L3_JOINT_CNT_NONFINITE] += !isfinite(nd.score);
        return;
    }
    if (*count < L3_JOINT_BALL_BEAM) {
        pool[(*count)++] = nd;
        return;
    }
    uint32_t wi = 0U;
    for (uint32_t i = 1U; i < *count; i++) {
        if (l3_joint_ball_better(&pool[wi], &pool[i])) wi = i;
    }
    if (l3_joint_ball_better(&nd, &pool[wi])) pool[wi] = nd;
}

static void l3_joint_write_point(
    const l3_joint_frame_t *fr, uint8_t tgt, float speed,
    l3_joint_point_t *out)
{
    const l3_joint_frame_target_t *t = &fr->targets[tgt];
    out->rangeBin     = t->rangeBin;
    out->speedMps     = speed;
    out->azimuthRad   = t->azimuthRad;
    out->elevationRad = t->elevationRad;
    out->timestampUs  = fr->timestampUs;
    out->anglesValid  = t->anglesValid;
}

static void l3_joint_write_club_slot(l3_joint_t *js, uint32_t headSlot,
                                    uint32_t oldestSlot, uint32_t depth, uint8_t forced)
{
    const l3_joint_club_link_t *head = &js->clubLinks[headSlot];
    const l3_joint_frame_t     *fr   = &js->frames[oldestSlot];
    if (head->count == 0U) return;
    if (forced) js->counters[L3_JOINT_CNT_FORCED_OUT]++;
    uint32_t best = l3_joint_best_club(head);
    const l3_joint_club_node_t *an =
        l3_joint_club_ancestor_node(js, headSlot, best, depth);
    if (an->target != L3_JOINT_NONE && js->clubCount < L3_JOINT_BALL_POINTS) {
        l3_joint_write_point(fr, an->target, an->speedMps,
                             &js->clubPoints[js->clubCount++]);
    }
}

static void l3_joint_write_ball_slot(l3_joint_t *js, uint32_t headSlot,
                                    uint32_t oldestSlot, uint32_t depth, uint8_t forced)
{
    const l3_joint_ball_link_t *head = &js->ballLinks[headSlot];
    const l3_joint_frame_t     *fr   = &js->frames[oldestSlot];
    if (head->count == 0U) return;
    if (forced) js->counters[L3_JOINT_CNT_FORCED_OUT]++;
    uint32_t best = l3_joint_best_ball(head);
    const l3_joint_ball_node_t *an =
        l3_joint_ball_ancestor_node(js, headSlot, best, depth);
    if (an->target != L3_JOINT_NONE && js->ballCount < L3_JOINT_BALL_POINTS) {
        l3_joint_ball_point_t *bp = &js->ballPoints[js->ballCount++];
        const l3_joint_frame_target_t *t = &fr->targets[an->target];
        bp->rangeBin     = t->rangeBin;
        bp->speedMps     = an->speedMps;
        bp->azimuthRad   = t->azimuthRad;
        bp->elevationRad = t->elevationRad;
        bp->timestampUs  = fr->timestampUs;
        bp->anglesValid  = t->anglesValid;
    }
}

static void l3_joint_process_oldest_club(l3_joint_t *js, uint32_t headSlot)
{
    if (js->winSize < L3_JOINT_WINDOW) return;
    uint32_t oldestDepth = js->winSize - 1U;
    uint32_t oldestSlot  = l3_joint_slot_at(headSlot, oldestDepth);
    int32_t agreed = l3_joint_club_agreed(js, headSlot, oldestDepth);
    l3_joint_write_club_slot(js, headSlot, oldestSlot, oldestDepth, agreed ? 0U : 1U);
    if (agreed) return;
    l3_joint_club_link_t *head = &js->clubLinks[headSlot];
    uint32_t best = l3_joint_best_club(head);
    uint8_t bestClub = l3_joint_club_ancestor(js, headSlot, best, oldestDepth);
    uint32_t kept = 0U;
    for (uint32_t i = 0U; i < head->count; i++) {
        if (l3_joint_club_ancestor(js, headSlot, i, oldestDepth) == bestClub) {
            if (kept != i) head->nodes[kept] = head->nodes[i];
            kept++;
        }
    }
    head->count = kept;
}

static void l3_joint_process_oldest_ball(l3_joint_t *js, uint32_t headSlot)
{
    if (js->winSize < L3_JOINT_WINDOW) return;
    uint32_t oldestDepth = js->winSize - 1U;
    uint32_t oldestSlot  = l3_joint_slot_at(headSlot, oldestDepth);
    int32_t agreed = l3_joint_ball_agreed(js, headSlot, oldestDepth);
    l3_joint_write_ball_slot(js, headSlot, oldestSlot, oldestDepth, agreed ? 0U : 1U);
    if (agreed) return;
    l3_joint_ball_link_t *head = &js->ballLinks[headSlot];
    uint32_t best = l3_joint_best_ball(head);
    uint8_t bestBall = l3_joint_ball_ancestor(js, headSlot, best, oldestDepth);
    uint32_t kept = 0U;
    for (uint32_t i = 0U; i < head->count; i++) {
        if (l3_joint_ball_ancestor(js, headSlot, i, oldestDepth) == bestBall) {
            if (kept != i) head->nodes[kept] = head->nodes[i];
            kept++;
        }
    }
    head->count = kept;
}

/* --------------------------------------------------------------------------
 * Confirmation: point 0 is the from-rest first touch and is exempt from
 * the min/max speed gate that applies to later points.
 * -------------------------------------------------------------------------- */

static int32_t l3_joint_try_confirm(l3_joint_t *js)
{
    const l3_joint_cfg_t *cfg = &js->cfg;
    if (js->ballConfirmed) return 1;
    if (js->ballCount < cfg->ballMinPoints) return 0;

    for (uint32_t i = 1U; i < js->ballCount; i++) {
        float sp = js->ballPoints[i].speedMps;
        if (sp < cfg->ballMinSpeedMps || sp > cfg->ballMaxSpeedMps) return 0;
    }

    if (js->ballCount >= 1U) {
        float t0ms = (float)(int32_t)(js->ballPoints[0].timestampUs
                                    - js->gateTimestampUs) * 1.0e-3F;
        if (t0ms > cfg->maxOriginCrossMs) return 0;
    }

    if (js->ballCount >= 2U) {
        float sumT=0,sumB=0,sumTT=0,sumTB=0;
        float t0s = (float)(int32_t)js->ballPoints[0].timestampUs * 1.0e-6F;
        float n   = (float)js->ballCount;
        for (uint32_t i = 0U; i < js->ballCount; i++) {
            float ti = (float)(int32_t)js->ballPoints[i].timestampUs*1.0e-6F - t0s;
            float bi = js->ballPoints[i].rangeBin;
            sumT+=ti; sumB+=bi; sumTT+=ti*ti; sumTB+=ti*bi;
        }
        float det = n*sumTT - sumT*sumT;
        if (fabsf(det) > 1.0e-9F) {
            float slope = (n*sumTB - sumT*sumB)/det;
            float intercept = (sumB - slope*sumT)/n;
            float ssq = 0.0F;
            for (uint32_t i = 0U; i < js->ballCount; i++) {
                float ti = (float)(int32_t)js->ballPoints[i].timestampUs*1.0e-6F - t0s;
                float err = js->ballPoints[i].rangeBin - (intercept + slope*ti);
                ssq += err*err;
            }
            if (sqrtf(ssq/n) > cfg->maxResidualBins) return 0;
        }
    }

    js->ballConfirmed = 1U;
    js->counters[L3_JOINT_CNT_CONFIRMED]++;
    return 1;
}

/* --------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */

void l3_joint_cfg_defaults(l3_joint_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->binWidthM          = 6.0F / 128.0F;
    cfg->velocitySpanMps    = 0.0F;
    cfg->startBehindBins    = 1.0F;
    cfg->startBeyondBins    = 10.0F;
    cfg->ballMinSpeedMps    = 10.0F;
    cfg->ballMaxSpeedMps    = 100.0F;
    cfg->ballAccelMps2      = 400.0F;
    cfg->ballMaxMisses      = 2U;
    cfg->ballMinPoints      = 4U;
    cfg->clubMinSpeedMps    = 0.0F;
    cfg->clubMaxSpeedMps    = 70.0F;
    cfg->clubDecelMps2      = 500.0F;
    cfg->clubAccelMps2      = 20.0F;
    cfg->clubMaxMisses      = 3U;
    cfg->rangeSigmaBins     = 1.0F;
    cfg->coastSigmaGrowBins = 0.5F;
    cfg->termCap            = 4.0F;
    cfg->missCost           = 0.5F;
    cfg->clubStrongerBonus  = 0.05F;
    cfg->confirmMargin      = 0.5F;
    cfg->maxOriginCrossMs   = 15.0F;
    cfg->maxResidualBins    = 1.0F;
}

void l3_joint_init(l3_joint_t *js, const l3_joint_cfg_t *cfg)
{
    memset(js, 0, sizeof(*js));
    js->cfg = *cfg;
}

void l3_joint_reset(l3_joint_t *js)
{
    l3_joint_cfg_t saved = js->cfg;
    memset(js, 0, sizeof(*js));
    js->cfg = saved;
}

void l3_joint_arm(l3_joint_t *js, const l3_joint_kin_t *seed,
                  uint32_t gateTimestampUs)
{
    l3_joint_reset(js);
    js->gateTimestampUs = gateTimestampUs;
    if (seed != NULL) {
        js->seed      = *seed;
        js->seedValid = 1U;
    }
}

static uint32_t l3_joint_club_claimed(const l3_joint_t *js, uint32_t headSlot)
{
    const l3_joint_club_link_t *head = &js->clubLinks[headSlot];
    if (head->count == 0U) return 0U;
    uint8_t t = head->nodes[l3_joint_best_club(head)].target;
    if (t == L3_JOINT_NONE || t >= 32U) return 0U;
    return (1U << t);
}

int32_t l3_joint_update(l3_joint_t *js, uint32_t frame, uint32_t timestampUs,
                        const l3_target_obs_t *targets, uint32_t n)
{
    if (n > L3_OBS_MAX_TARGETS) n = L3_OBS_MAX_TARGETS;

    if (js->winSize == 0U) {
        js->winHead = 0U;
    } else {
        js->winHead = (js->winHead + 1U) % L3_JOINT_WINDOW;
    }
    if (js->winSize < L3_JOINT_WINDOW) js->winSize++;

    uint32_t headSlot = js->winHead;

    l3_joint_frame_t *fr = &js->frames[headSlot];
    fr->count       = n;
    fr->timestampUs = timestampUs;
    for (uint32_t i = 0U; i < n; i++) {
        fr->targets[i].rangeBin        = targets[i].rangeBin;
        fr->targets[i].dopplerAliasMps = targets[i].dopplerAliasMps;
        fr->targets[i].snr             = targets[i].snr;
        fr->targets[i].azimuthRad      = targets[i].azimuthRad;
        fr->targets[i].elevationRad    = targets[i].elevationRad;
        fr->targets[i].anglesValid     = targets[i].anglesValid;
    }

    uint32_t prevSlot = (headSlot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;

    float dtS = 0.0F;
    if (js->winSize > 1U) {
        dtS = l3_joint_dt_s(js->frames[prevSlot].timestampUs, timestampUs);
        if (dtS <= 0.0F) {
            js->counters[L3_JOINT_CNT_SKIPPED]++;
            js->clubLinks[headSlot] = js->clubLinks[prevSlot];
            js->ballLinks[headSlot] = js->ballLinks[prevSlot];
            js->clubLinks[headSlot].frame = frame;
            js->ballLinks[headSlot].frame = frame;
            js->clubLinks[headSlot].timestampUs = timestampUs;
            js->ballLinks[headSlot].timestampUs = timestampUs;
            return js->ballConfirmed ? 1 : 0;
        }
    }

    l3_joint_club_node_t clubPool[L3_JOINT_CLUB_BEAM];
    uint32_t clubPoolCount = 0U;
    l3_joint_ball_node_t ballPool[L3_JOINT_BALL_BEAM];
    uint32_t ballPoolCount = 0U;

    uint32_t nC = n + 1U;

    if (js->winSize == 1U) {
        l3_joint_club_node_t croot;
        memset(&croot, 0, sizeof(croot));
        croot.target  = L3_JOINT_NONE;
        croot.parent  = L3_JOINT_NONE;
        croot.score   = 0.0F;
        croot.state   = js->seedValid ? L3_JOINT_ACTIVE : L3_JOINT_UNSTARTED;
        croot.speedMps = js->seed.speedMps;
        js->clubLinks[headSlot].nodes[0] = croot;
        js->clubLinks[headSlot].count    = 1U;
        js->clubLinks[headSlot].frame    = frame;
        js->clubLinks[headSlot].timestampUs = timestampUs;

        for (uint32_t ci = 0U; ci < nC; ci++) {
            uint8_t cidx = (ci < n) ? (uint8_t)ci : L3_JOINT_NONE;
            l3_joint_club_node_t nd;
            l3_joint_score_club(js, headSlot, 0U, cidx, fr, dtS, &nd);
            POOL_TRY(clubPool, clubPoolCount, L3_JOINT_CLUB_BEAM, nd, score);
        }

        l3_joint_ball_node_t broot;
        memset(&broot, 0, sizeof(broot));
        broot.target  = L3_JOINT_NONE;
        broot.parent  = L3_JOINT_NONE;
        broot.score   = 0.0F;
        broot.state   = L3_JOINT_UNSTARTED;
        js->ballLinks[headSlot].nodes[0] = broot;
        js->ballLinks[headSlot].count    = 1U;
        js->ballLinks[headSlot].frame    = frame;
        js->ballLinks[headSlot].timestampUs = timestampUs;
    } else {
        const l3_joint_club_link_t *prev = &js->clubLinks[prevSlot];
        for (uint32_t pi = 0U; pi < prev->count; pi++) {
            for (uint32_t ci = 0U; ci < nC; ci++) {
                uint8_t cidx = (ci < n) ? (uint8_t)ci : L3_JOINT_NONE;
                l3_joint_club_node_t nd;
                l3_joint_score_club(js, prevSlot, pi, cidx, fr, dtS, &nd);
                POOL_TRY(clubPool, clubPoolCount, L3_JOINT_CLUB_BEAM, nd, score);
            }
        }
    }

    js->clubLinks[headSlot].count       = clubPoolCount;
    js->clubLinks[headSlot].frame       = frame;
    js->clubLinks[headSlot].timestampUs = timestampUs;
    if (clubPoolCount > 0U) {
        memcpy(js->clubLinks[headSlot].nodes, clubPool,
               clubPoolCount * sizeof(l3_joint_club_node_t));
    }

    uint32_t claimed = l3_joint_club_claimed(js, headSlot);

    if (js->winSize == 1U) {
        for (uint32_t bi = 0U; bi < nC; bi++) {
            uint8_t bidx = (bi < n) ? (uint8_t)bi : L3_JOINT_NONE;
            if (bidx != L3_JOINT_NONE && (claimed & (1U << bidx))) continue;
            l3_joint_ball_node_t nd;
            l3_joint_score_ball(js, headSlot, 0U, bidx, fr, dtS, &nd);
            l3_joint_ball_pool_try(js, ballPool, &ballPoolCount, nd);
        }
    } else {
        const l3_joint_ball_link_t *prev = &js->ballLinks[prevSlot];
        for (uint32_t pi = 0U; pi < prev->count; pi++) {
            for (uint32_t bi = 0U; bi < nC; bi++) {
                uint8_t bidx = (bi < n) ? (uint8_t)bi : L3_JOINT_NONE;
                if (bidx != L3_JOINT_NONE && (claimed & (1U << bidx))) continue;
                l3_joint_ball_node_t nd;
                l3_joint_score_ball(js, prevSlot, pi, bidx, fr, dtS, &nd);
                l3_joint_ball_pool_try(js, ballPool, &ballPoolCount, nd);
            }
        }
    }

    js->ballLinks[headSlot].count       = ballPoolCount;
    js->ballLinks[headSlot].frame       = frame;
    js->ballLinks[headSlot].timestampUs = timestampUs;
    if (ballPoolCount > 0U) {
        memcpy(js->ballLinks[headSlot].nodes, ballPool,
               ballPoolCount * sizeof(l3_joint_ball_node_t));
    }

    l3_joint_process_oldest_club(js, headSlot);
    l3_joint_process_oldest_ball(js, headSlot);
    l3_joint_try_confirm(js);
    return js->ballConfirmed ? 1 : 0;
}

void l3_joint_finish(l3_joint_t *js)
{
    if (js->winSize == 0U) return;
    uint32_t head = js->winHead;
    uint32_t first = (js->winSize < L3_JOINT_WINDOW)
                   ? (js->winSize - 1U)
                   : (js->winSize - 2U);
    for (int32_t d = (int32_t)first; d >= 0; d--) {
        uint32_t depth = (uint32_t)d;
        uint32_t slot  = l3_joint_slot_at(head, depth);
        l3_joint_write_club_slot(js, head, slot, depth, 1U);
        l3_joint_write_ball_slot(js, head, slot, depth, 1U);
    }
    l3_joint_try_confirm(js);
}

uint32_t l3_joint_angle_requests(l3_joint_t *js,
                                 l3_joint_angle_req_t *out, uint32_t maxOut)
{
    uint32_t count = 0U;
    uint32_t headSlot = js->winHead;
    const l3_joint_club_link_t *club = &js->clubLinks[headSlot];
    const l3_joint_ball_link_t *ball = &js->ballLinks[headSlot];
    const l3_joint_frame_t     *fr   = &js->frames[headSlot];

    uint8_t used[L3_OBS_MAX_TARGETS];
    memset(used, 0, sizeof(used));
    for (uint32_t i = 0U; i < club->count; i++) {
        if (club->nodes[i].target != L3_JOINT_NONE)
            used[club->nodes[i].target] = 1U;
    }
    for (uint32_t i = 0U; i < ball->count; i++) {
        if (ball->nodes[i].target != L3_JOINT_NONE)
            used[ball->nodes[i].target] = 1U;
    }
    for (uint32_t ti = 0U; ti < fr->count && count < maxOut; ti++) {
        if (used[ti] && !fr->targets[ti].anglesValid) {
            out[count].frameSlot = (uint8_t)headSlot;
            out[count].targetIdx = (uint8_t)ti;
            out[count].needed    = 1U;
            count++;
        }
    }
    js->counters[L3_JOINT_CNT_ANGLE_EST] += count;
    return count;
}

void l3_joint_set_angles(l3_joint_t *js,
                         const l3_joint_angle_req_t *reqs, uint32_t n)
{
    (void)js; (void)reqs; (void)n;
}

l3_joint_now_t l3_joint_now(const l3_joint_t *js)
{
    l3_joint_now_t out;
    memset(&out, 0, sizeof(out));
    out.clubBin     = -1.0F;
    out.ballBin     = -1.0F;
    out.clubPredBin = -1.0F;
    out.ballPredBin = -1.0F;
    out.ballConfirmed = js->ballConfirmed;
    if (js->winSize == 0U) return out;

    const l3_joint_club_link_t *club = &js->clubLinks[js->winHead];
    const l3_joint_ball_link_t *ball = &js->ballLinks[js->winHead];
    const l3_joint_frame_t     *fr   = &js->frames[js->winHead];
    out.beamSize = club->count + ball->count;

    if (club->count > 0U) {
        uint32_t best = l3_joint_best_club(club);
        const l3_joint_club_node_t *bn = &club->nodes[best];
        out.bestScore += bn->score;
        if (bn->target != L3_JOINT_NONE && bn->target < fr->count)
            out.clubBin = fr->targets[bn->target].rangeBin;
        out.clubPredBin = bn->speedMps;
    }
    if (ball->count > 0U) {
        uint32_t best = l3_joint_best_ball(ball);
        const l3_joint_ball_node_t *bn = &ball->nodes[best];
        out.bestScore += bn->score;
        if (bn->target != L3_JOINT_NONE && bn->target < fr->count)
            out.ballBin = fr->targets[bn->target].rangeBin;
        out.ballPredBin = bn->speedMps;
    }
    return out;
}

uint32_t l3_joint_target_use(const l3_joint_t *js, uint32_t targetIdx)
{
    if (js->winSize == 0U) return L3_JOINT_USE_NONE;
    const l3_joint_club_link_t *club = &js->clubLinks[js->winHead];
    const l3_joint_ball_link_t *ball = &js->ballLinks[js->winHead];
    if (club->count > 0U) {
        uint32_t best = l3_joint_best_club(club);
        if (club->nodes[best].target != L3_JOINT_NONE &&
            (uint32_t)club->nodes[best].target == targetIdx)
            return L3_JOINT_USE_CLUB;
    }
    if (ball->count > 0U) {
        uint32_t best = l3_joint_best_ball(ball);
        if (ball->nodes[best].target != L3_JOINT_NONE &&
            (uint32_t)ball->nodes[best].target == targetIdx)
            return L3_JOINT_USE_BALL;
    }
    return L3_JOINT_USE_NONE;
}

uint32_t l3_joint_window_ball(const l3_joint_t *js,
                              l3_joint_ball_point_t *out, uint32_t maxOut)
{
    uint32_t count = 0U;
    for (uint32_t i = 0U; i < js->ballCount && count < maxOut; i++)
        out[count++] = js->ballPoints[i];
    return count;
}

int32_t l3_joint_ball_point(const l3_joint_t *js, uint32_t slotIdx,
                             l3_joint_ball_point_t *out)
{
    if (slotIdx >= js->ballCount) return 0;
    *out = js->ballPoints[slotIdx];
    return 1;
}

int32_t l3_joint_launch(const l3_joint_t *js, const l3_delivery_t *clubFit,
                        uint32_t impactTimestampUs, l3_launch_t *out)
{
    if (!js->ballConfirmed || js->ballCount < 2U) return 0;
    memset(out, 0, sizeof(*out));
    out->lateFrom = L3_LAUNCH_NO_LATE;
    out->points = js->ballCount;
    float sumSpeed = 0.0F;
    uint32_t n = 0U;
    for (uint32_t i = 1U; i < js->ballCount; i++) {
        sumSpeed += js->ballPoints[i].speedMps;
        n++;
    }
    if (n > 0U) out->speedMps = sumSpeed / (float)n;
    out->radialSpeedMps = out->speedMps;
    out->speedValid = (out->speedMps > 0.0F) ? 1U : 0U;
    (void)clubFit; (void)impactTimestampUs;
    return 1;
}

uint32_t l3_joint_struct_bytes(void)
{
    return (uint32_t)sizeof(l3_joint_t);
}
