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

uint32_t l3_dsp_request_check(const l3_dsp_request_t *request, uint32_t l3Bytes)
{
    uint32_t frameBytes;

    if (request == NULL || request->magic != L3_DSP_MAGIC) {
        return L3_DSP_ERR_MAGIC;
    }
    if (request->cmd == L3_DSP_CMD_PING) {
        return L3_DSP_OK;
    }
    if (request->cmd != L3_DSP_CMD_PROBE) {
        return L3_DSP_ERR_CMD;
    }
    if (request->ntx == 0U || request->ntx > L3_BIN_SCORE_MAX_TX || request->loops == 0U ||
        request->loops > L3_IQ16_MAX_LOOPS || request->binCount == 0U ||
        request->binCount > 256U || request->nBins == 0U ||
        request->firstBin >= request->binCount ||
        request->nBins > request->binCount - request->firstBin) {
        return L3_DSP_ERR_GEOMETRY;
    }
    frameBytes = l3_dsp_frame_bytes(request->ntx, L3_DSP_N_RX, request->binCount, request->loops);
    /* Word aligned; subtraction, not addition, so a huge offset cannot wrap. */
    if ((request->frameOffset & 3U) != 0U || frameBytes > l3Bytes ||
        request->frameOffset > l3Bytes - frameBytes) {
        return L3_DSP_ERR_RANGE;
    }
    return L3_DSP_OK;
}

void l3_dsp_probe_run(const l3_dsp_request_t *request, const uint8_t *l3Base, uint32_t l3Bytes,
                      l3_dsp_reply_t *reply)
{
    uint32_t bin;

    memset(reply, 0, sizeof(*reply));
    reply->magic = L3_DSP_MAGIC;
    if (request == NULL) {
        reply->status = L3_DSP_ERR_MAGIC;
        return;
    }
    reply->cmd = request->cmd;
    reply->seq = request->seq;
    reply->status = l3_dsp_request_check(request, l3Bytes);
    if (reply->status != L3_DSP_OK || request->cmd != L3_DSP_CMD_PROBE) {
        return;
    }
    if (l3Base == NULL) {
        reply->status = L3_DSP_ERR_RANGE;
        return;
    }
    for (bin = request->firstBin; bin < request->firstBin + request->nBins; bin++) {
        l3_bin_obs_t obs;
        const int16_t *frame = (const int16_t *)(const void *)&l3Base[request->frameOffset];

        if (l3_bin_score_iq16(frame, request->binCount, bin, request->ntx, L3_DSP_N_RX,
                              request->loops, &obs, NULL) == 0) {
            reply->energySum += obs.energy;
            reply->r1ReSum += obs.r1Re;
            reply->r1ImSum += obs.r1Im;
            reply->nBins++;
        }
    }
}
