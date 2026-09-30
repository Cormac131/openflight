/* IWR6843 MSS <-> DSS detect link. See l3_dsp_ipc.h. */
#include "l3_dsp_ipc.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "l3_bin_score.h"
#include "l3_iq16_stats.h"

uint32_t l3_dsp_request_size(void)
{
    return (uint32_t)sizeof(l3_dsp_request_t);
}

uint32_t l3_dsp_reply_size(void)
{
    return (uint32_t)sizeof(l3_dsp_reply_t);
}

uint32_t l3_dsp_status_size(void)
{
    return (uint32_t)sizeof(l3_dsp_status_t);
}

static const char *l3_dsp_stage_name(uint32_t stage)
{
    switch (stage) {
    case L3_DSP_STAGE_RESET:
        return "reset";
    case L3_DSP_STAGE_FIRST:
        return "startup_first";
    case L3_DSP_STAGE_LAST:
        return "startup_last";
    case L3_DSP_STAGE_EXCEPTION:
        return "exception";
    case L3_DSP_STAGE_MAIN:
        return "main";
    case L3_DSP_STAGE_SOC:
        return "soc_init";
    case L3_DSP_STAGE_TASK:
        return "task";
    case L3_DSP_STAGE_MAILBOX:
        return "mailbox_init";
    case L3_DSP_STAGE_LINK:
        return "link_open";
    default:
        return NULL;
    }
}

int32_t l3_dsp_status_format(const l3_dsp_status_t *status, char *out, uint32_t cap)
{
    const char *name;
    char number[12];
    uint32_t stage;

    if (status == NULL || status->magic != L3_DSP_STATUS_MAGIC) {
        return (int32_t)snprintf(out, cap, "dsp status stage=never_booted magic=%08x",
                                 status == NULL ? 0U : (unsigned)status->magic);
    }
    stage = status->stage & ~L3_DSP_STAGE_FAILED;
    name = l3_dsp_stage_name(stage);
    if (name == NULL) {
        (void)snprintf(number, sizeof(number), "%u", (unsigned)status->stage);
        name = number;
    }
    if (status->excFlags != 0U || status->excPc != 0U) {
        return (int32_t)snprintf(out, cap,
                                 "dsp status stage=%s%s err=%d beats=%u served=%u "
                                 "exc_pc=%08x exc_efr=%08x",
                                 name,
                                 (status->stage & L3_DSP_STAGE_FAILED) != 0U ? " FAILED" : "",
                                 (int)status->errCode, (unsigned)status->heartbeat,
                                 (unsigned)status->served, (unsigned)status->excPc,
                                 (unsigned)status->excFlags);
    }
    return (int32_t)snprintf(out, cap, "dsp status stage=%s%s err=%d beats=%u served=%u", name,
                             (status->stage & L3_DSP_STAGE_FAILED) != 0U ? " FAILED" : "",
                             (int)status->errCode, (unsigned)status->heartbeat,
                             (unsigned)status->served);
}

uint32_t l3_dsp_hw_size(void)
{
    return (uint32_t)sizeof(l3_dsp_hw_t);
}

int32_t l3_dsp_hw_format(const l3_dsp_hw_t *hw, char *out, uint32_t cap)
{
    char stage[24];

    if ((hw->gpreg & L3_DSP_GPREG_TAG_MASK) != L3_DSP_GPREG_TAG) {
        (void)snprintf(stage, sizeof(stage), "none(%08x)", (unsigned)hw->gpreg);
    } else {
        uint32_t code = hw->gpreg & 0xFFU;
        const char *name = l3_dsp_stage_name(code & ~L3_DSP_STAGE_FAILED);

        if (name != NULL) {
            (void)snprintf(stage, sizeof(stage), "%s%s", name,
                           (code & L3_DSP_STAGE_FAILED) != 0U ? "!FAILED" : "");
        } else {
            (void)snprintf(stage, sizeof(stage), "%u", (unsigned)code);
        }
    }
    return (int32_t)snprintf(out, cap,
                             "dsp hw gpreg_stage=%s halt=%u power=%u stc=%u "
                             "esm=%08x,%08x,%08x,%08x hsram=%s",
                             stage, (unsigned)hw->halt, (unsigned)hw->power, (unsigned)hw->stc,
                             (unsigned)hw->esm[0], (unsigned)hw->esm[1], (unsigned)hw->esm[2],
                             (unsigned)hw->esm[3], hw->hsramOk != 0U ? "ok" : "BAD");
}

uint32_t l3_dsp_frame_bytes(uint32_t ntx, uint32_t nrx, uint32_t binCount, uint32_t loops)
{
    return loops * ntx * nrx * binCount * 2U * (uint32_t)sizeof(int16_t);
}

/* SCORE's spans: at least one, at most L3_DSP_MAX_SPANS, each non-empty and
 * inside both the frame and the detector's L3_DSP_MAX_BINS. */
static uint32_t l3_dsp_spans_check(const l3_dsp_request_t *request)
{
    uint32_t limit = (request->binCount < L3_DSP_MAX_BINS) ? request->binCount : L3_DSP_MAX_BINS;
    uint32_t k;

    if (request->nSpans == 0U || request->nSpans > L3_DSP_MAX_SPANS) {
        return L3_DSP_ERR_SPANS;
    }
    for (k = 0U; k < request->nSpans; k++) {
        /* Subtraction, not addition, so a huge count cannot wrap. */
        if (request->spanCount[k] == 0U || request->spanFirst[k] >= limit ||
            request->spanCount[k] > limit - request->spanFirst[k]) {
            return L3_DSP_ERR_SPANS;
        }
    }
    return L3_DSP_OK;
}

uint32_t l3_dsp_request_check(const l3_dsp_request_t *request, uint32_t l3Bytes)
{
    uint32_t frameBytes;

    if (request == NULL || request->magic != L3_DSP_MAGIC) {
        return L3_DSP_ERR_MAGIC;
    }
    if (request->cmd == L3_DSP_CMD_PING) {
        return L3_DSP_OK;
    }
    if (request->cmd != L3_DSP_CMD_PROBE && request->cmd != L3_DSP_CMD_SCORE) {
        return L3_DSP_ERR_CMD;
    }
    if (request->ntx == 0U || request->ntx > L3_BIN_SCORE_MAX_TX || request->loops == 0U ||
        request->loops > L3_IQ16_MAX_LOOPS || request->binCount == 0U ||
        request->binCount > 256U) {
        return L3_DSP_ERR_GEOMETRY;
    }
    if (request->cmd == L3_DSP_CMD_PROBE &&
        (request->nBins == 0U || request->firstBin >= request->binCount ||
         request->nBins > request->binCount - request->firstBin)) {
        return L3_DSP_ERR_GEOMETRY;
    }
    frameBytes = l3_dsp_frame_bytes(request->ntx, L3_DSP_N_RX, request->binCount, request->loops);
    /* Word aligned; subtraction, not addition, so a huge offset cannot wrap. */
    if ((request->frameOffset & 3U) != 0U || frameBytes > l3Bytes ||
        request->frameOffset > l3Bytes - frameBytes) {
        return L3_DSP_ERR_RANGE;
    }
    if (request->cmd == L3_DSP_CMD_SCORE) {
        return l3_dsp_spans_check(request);
    }
    return L3_DSP_OK;
}

/* The reply's header for a request: magic, cmd, seq, and the request's own
 * status against an L3 of l3Bytes. */
static void l3_dsp_reply_begin(const l3_dsp_request_t *request, uint32_t l3Bytes,
                               l3_dsp_reply_t *reply)
{
    memset(reply, 0, sizeof(*reply));
    reply->magic = L3_DSP_MAGIC;
    if (request == NULL) {
        reply->status = L3_DSP_ERR_MAGIC;
        return;
    }
    reply->cmd = request->cmd;
    reply->seq = request->seq;
    reply->status = l3_dsp_request_check(request, l3Bytes);
}

/* PROBE's bins summed through the shared scorer: in place or gathered, the
 * one loop, so the two cannot drift apart. */
static void l3_dsp_probe_score(const l3_dsp_request_t *request, l3_dsp_iq16_ctx_t *frame,
                               l3_dsp_reply_t *reply)
{
    uint32_t bin;

    for (bin = request->firstBin; bin < request->firstBin + request->nBins; bin++) {
        l3_bin_obs_t obs;

        if (l3_dsp_iq16_scorer(frame, bin, &obs) == 0) {
            reply->energySum += obs.energy;
            reply->r1ReSum += obs.r1Re;
            reply->r1ImSum += obs.r1Im;
            reply->nBins++;
        }
    }
}

static void l3_dsp_frame_ctx(const l3_dsp_request_t *request, const uint8_t *base,
                             uint32_t binCount, uint32_t binBase, l3_dsp_iq16_ctx_t *frame)
{
    frame->frame = (const int16_t *)(const void *)base;
    frame->binCount = binCount;
    frame->ntx = request->ntx;
    frame->loops = request->loops;
    frame->binBase = binBase;
}

void l3_dsp_probe_run(const l3_dsp_request_t *request, const uint8_t *l3Base, uint32_t l3Bytes,
                      l3_dsp_reply_t *reply)
{
    l3_dsp_iq16_ctx_t frame;

    l3_dsp_reply_begin(request, l3Bytes, reply);
    if (request == NULL || reply->status != L3_DSP_OK || request->cmd != L3_DSP_CMD_PROBE) {
        return;
    }
    if (l3Base == NULL) {
        reply->status = L3_DSP_ERR_RANGE;
        return;
    }
    l3_dsp_frame_ctx(request, &l3Base[request->frameOffset], request->binCount, 0U, &frame);
    l3_dsp_probe_score(request, &frame, reply);
}

/* --- SCORE ------------------------------------------------------------------ */

uint32_t l3_dsp_result_size(void)
{
    return (uint32_t)sizeof(l3_dsp_result_t);
}

int32_t l3_dsp_iq16_scorer(void *ctx, uint32_t localBin, l3_bin_obs_t *out)
{
    const l3_dsp_iq16_ctx_t *frame = (const l3_dsp_iq16_ctx_t *)ctx;

    if (frame == NULL || localBin < frame->binBase) {
        return -1;
    }
    return l3_bin_score_iq16(frame->frame, frame->binCount, localBin - frame->binBase, frame->ntx,
                             L3_DSP_N_RX, frame->loops, out, NULL);
}

static uint32_t l3_dsp_bin_limit(uint32_t binCount)
{
    return (binCount < L3_DSP_MAX_BINS) ? binCount : L3_DSP_MAX_BINS;
}

static uint32_t l3_dsp_marked(const uint32_t *bitmap, uint32_t bin)
{
    return (bitmap[bin >> 5U] >> (bin & 31U)) & 1U;
}

static void l3_dsp_mark(uint32_t *bitmap, uint32_t bin)
{
    bitmap[bin >> 5U] |= (uint32_t)(1UL << (bin & 31U));
}

uint32_t l3_dsp_spans_localize(uint32_t binStart, uint32_t binCount, const l3_span_t *global,
                               uint32_t n, l3_span_t *local)
{
    uint32_t limit = l3_dsp_bin_limit(binCount);
    uint32_t kept = 0U;
    uint32_t k;

    if (global == NULL || local == NULL || n > L3_DSP_MAX_SPANS) {
        return 0U;
    }
    for (k = 0U; k < n; k++) {
        uint32_t first = global[k].first;
        uint32_t end = global[k].first + global[k].count; /* one past, global */

        if (global[k].count == 0U || end < first) {
            continue; /* empty, or wrapped */
        }
        if (first < binStart) {
            first = binStart;
        }
        if (end > binStart + limit) {
            end = binStart + limit;
        }
        if (end <= first) {
            continue; /* entirely outside the frame */
        }
        local[kept].first = first - binStart;
        local[kept].count = end - first;
        kept++;
    }
    return kept;
}

uint32_t l3_dsp_spans_score(const l3_span_t *spans, uint32_t nSpans, uint32_t binCount,
                            l3_bin_scorer_fn scorer, void *ctx, l3_bin_obs_t *obs,
                            uint32_t *scored)
{
    uint32_t limit = l3_dsp_bin_limit(binCount);
    uint32_t newly = 0U;
    uint32_t k;

    if (spans == NULL || scorer == NULL || obs == NULL || scored == NULL) {
        return 0U;
    }
    for (k = 0U; k < nSpans; k++) {
        uint32_t bin;

        for (bin = spans[k].first; bin < limit && bin - spans[k].first < spans[k].count; bin++) {
            if (l3_dsp_marked(scored, bin) != 0U) {
                continue;
            }
            if (scorer(ctx, bin, &obs[bin]) == 0) {
                l3_dsp_mark(scored, bin);
                newly++;
            }
        }
    }
    return newly;
}

/* SCORE's answer: the result header, then (request accepted) its spans
 * scored through the shared scorer over frame -- in place or gathered. */
static void l3_dsp_score_spans(const l3_dsp_request_t *request, l3_dsp_iq16_ctx_t *frame,
                               l3_dsp_reply_t *reply, l3_dsp_result_t *result)
{
    l3_span_t spans[L3_DSP_MAX_SPANS];
    uint32_t k;

    memset(result, 0, sizeof(*result));
    result->magic = L3_DSP_RESULT_MAGIC;
    result->seq = request->seq;
    result->epoch = request->epoch;
    if (reply->status == L3_DSP_OK && (frame == NULL || frame->frame == NULL)) {
        reply->status = L3_DSP_ERR_RANGE;
    }
    result->status = reply->status;
    if (reply->status != L3_DSP_OK) {
        return;
    }
    for (k = 0U; k < request->nSpans; k++) {
        spans[k].first = request->spanFirst[k];
        spans[k].count = request->spanCount[k];
    }
    result->count = l3_dsp_spans_score(spans, request->nSpans, request->binCount,
                                       l3_dsp_iq16_scorer, frame, result->obs, result->scored);
    reply->nBins = result->count;
}

void l3_dsp_serve(const l3_dsp_request_t *request, const uint8_t *l3Base, uint32_t l3Bytes,
                  l3_dsp_reply_t *reply, l3_dsp_result_t *result)
{
    l3_dsp_iq16_ctx_t frame;

    if (request == NULL || request->cmd != L3_DSP_CMD_SCORE) {
        l3_dsp_probe_run(request, l3Base, l3Bytes, reply);
        return;
    }
    l3_dsp_reply_begin(request, l3Bytes, reply);
    if (result == NULL) {
        reply->status = L3_DSP_ERR_RANGE;
        return;
    }
    if (l3Base != NULL && reply->status == L3_DSP_OK) {
        l3_dsp_frame_ctx(request, &l3Base[request->frameOffset], request->binCount, 0U, &frame);
        l3_dsp_score_spans(request, &frame, reply, result);
    } else {
        l3_dsp_score_spans(request, NULL, reply, result);
    }
}

/* --- the gather ---------------------------------------------------------------- */

uint32_t l3_dsp_gather_size(void)
{
    return (uint32_t)sizeof(l3_dsp_gather_t);
}

uint32_t l3_dsp_gather_plan(const l3_dsp_request_t *request, uint32_t l3Bytes, uint32_t maxBytes,
                            l3_dsp_gather_t *out)
{
    uint32_t status;
    uint32_t lo;
    uint32_t hi;
    uint32_t k;

    memset(out, 0, sizeof(*out));
    status = l3_dsp_request_check(request, l3Bytes);
    if (status != L3_DSP_OK) {
        return status;
    }
    if (request->cmd == L3_DSP_CMD_PROBE) {
        lo = request->firstBin;
        hi = request->firstBin + request->nBins;
    } else if (request->cmd == L3_DSP_CMD_SCORE) {
        uint32_t limit = l3_dsp_bin_limit(request->binCount);

        lo = limit;
        hi = 0U;
        for (k = 0U; k < request->nSpans; k++) {
            uint32_t first = request->spanFirst[k];
            uint32_t end = first + request->spanCount[k];

            if (end > limit) {
                end = limit;
            }
            if (first < lo) {
                lo = first;
            }
            if (end > hi) {
                hi = end;
            }
        }
        if (hi <= lo) {
            return L3_DSP_ERR_SPANS;
        }
    } else {
        return L3_DSP_ERR_CMD;
    }
    out->lo = lo;
    out->width = hi - lo;
    out->rows = request->loops * request->ntx * L3_DSP_N_RX;
    out->srcStride = request->binCount * 4U;
    out->srcOffset = request->frameOffset + lo * 4U;
    out->rowBytes = out->width * 4U;
    out->bytes = out->rows * out->rowBytes;
    if (out->bytes > maxBytes || out->rowBytes > L3_DSP_EDMA_MAX_BIDX ||
        out->srcStride > L3_DSP_EDMA_MAX_BIDX || out->rows > 0xFFFFU) {
        memset(out, 0, sizeof(*out));
        return L3_DSP_ERR_GATHER;
    }
    return L3_DSP_OK;
}

void l3_dsp_gather_copy(const uint8_t *l3Base, const l3_dsp_gather_t *gather, uint8_t *dst)
{
    uint32_t row;

    for (row = 0U; row < gather->rows; row++) {
        memcpy(&dst[row * gather->rowBytes],
               &l3Base[gather->srcOffset + row * gather->srcStride], gather->rowBytes);
    }
}

void l3_dsp_serve_gathered(const l3_dsp_request_t *request, const l3_dsp_gather_t *gather,
                           const uint8_t *copy, l3_dsp_reply_t *reply, l3_dsp_result_t *result)
{
    l3_dsp_iq16_ctx_t frame;
    uint8_t usable;

    /* The request was checked against L3 when the gather was planned; here
     * only its header matters, and the copy stands in for the frame. */
    l3_dsp_reply_begin(request, 0xFFFFFFFFU, reply);
    reply->gathered = 1U;
    usable = (uint8_t)(request != NULL && gather != NULL && copy != NULL && gather->width > 0U);
    if (usable) {
        l3_dsp_frame_ctx(request, copy, gather->width, gather->lo, &frame);
    }
    if (request != NULL && request->cmd == L3_DSP_CMD_SCORE) {
        if (result == NULL) {
            reply->status = L3_DSP_ERR_RANGE;
            return;
        }
        l3_dsp_score_spans(request, usable ? &frame : NULL, reply, result);
        return;
    }
    if (request == NULL || reply->status != L3_DSP_OK || request->cmd != L3_DSP_CMD_PROBE) {
        return;
    }
    if (!usable) {
        reply->status = L3_DSP_ERR_RANGE;
        return;
    }
    l3_dsp_probe_score(request, &frame, reply);
}

static uint32_t l3_dsp_popcount(const uint32_t *bitmap)
{
    uint32_t count = 0U;
    uint32_t bin;

    for (bin = 0U; bin < L3_DSP_MAX_BINS; bin++) {
        count += l3_dsp_marked(bitmap, bin);
    }
    return count;
}

uint32_t l3_dsp_result_check(const l3_dsp_result_t *result, uint32_t seq, uint32_t epoch)
{
    if (result == NULL || result->magic != L3_DSP_RESULT_MAGIC || result->seq != seq ||
        result->epoch != epoch) {
        return L3_DSP_ERR_STALE;
    }
    if (result->status != L3_DSP_OK) {
        return result->status;
    }
    if (result->count != l3_dsp_popcount(result->scored)) {
        return L3_DSP_ERR_STALE; /* torn or corrupt: never merged */
    }
    return L3_DSP_OK;
}

uint32_t l3_dsp_result_merge(const l3_dsp_result_t *result, l3_bin_obs_t *obs,
                             uint32_t *scored)
{
    uint32_t merged = 0U;
    uint32_t bin;

    if (result == NULL || obs == NULL || scored == NULL) {
        return 0U;
    }
    for (bin = 0U; bin < L3_DSP_MAX_BINS; bin++) {
        if (l3_dsp_marked(result->scored, bin) != 0U && l3_dsp_marked(scored, bin) == 0U) {
            obs[bin] = result->obs[bin];
            l3_dsp_mark(scored, bin);
            merged++;
        }
    }
    return merged;
}

static uint32_t l3_dsp_bits(float value)
{
    uint32_t bits;

    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

int32_t l3_dsp_result_compare(const l3_dsp_result_t *result, const l3_bin_obs_t *mss,
                              const uint32_t *mssScored, uint32_t *bin, uint32_t *field)
{
    uint32_t local;

    if (result == NULL || mss == NULL || mssScored == NULL) {
        return 0;
    }
    for (local = 0U; local < L3_DSP_MAX_BINS; local++) {
        uint32_t which = L3_DSP_FIELD_SET;
        uint32_t onDss = l3_dsp_marked(result->scored, local);

        if (onDss != l3_dsp_marked(mssScored, local)) {
            which = L3_DSP_FIELD_SET;
        } else if (onDss == 0U) {
            continue;
        } else if (l3_dsp_bits(result->obs[local].energy) != l3_dsp_bits(mss[local].energy)) {
            which = L3_DSP_FIELD_ENERGY;
        } else if (l3_dsp_bits(result->obs[local].peak) != l3_dsp_bits(mss[local].peak)) {
            which = L3_DSP_FIELD_PEAK;
        } else if (l3_dsp_bits(result->obs[local].loop0) != l3_dsp_bits(mss[local].loop0)) {
            which = L3_DSP_FIELD_LOOP0;
        } else if (l3_dsp_bits(result->obs[local].r1Re) != l3_dsp_bits(mss[local].r1Re)) {
            which = L3_DSP_FIELD_R1RE;
        } else if (l3_dsp_bits(result->obs[local].r1Im) != l3_dsp_bits(mss[local].r1Im)) {
            which = L3_DSP_FIELD_R1IM;
        } else {
            continue;
        }
        if (bin != NULL) {
            *bin = local;
        }
        if (field != NULL) {
            *field = which;
        }
        return 0;
    }
    return 1;
}
