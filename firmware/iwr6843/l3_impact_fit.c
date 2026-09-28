/* See l3_impact_fit.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_impact_fit.h"
#include "l3_text.h"

void l3_impact_fit_cfg_defaults(l3_impact_fit_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->binWidthM = 6.0F / 128.0F;
    cfg->bandBins = 6.0F;         /* the ridge on the 2026-09-28 capture */
    cfg->fitPoints = 4U;          /* about 12 ms at 3 ms frames */
    cfg->minPoints = 3U;          /* a line and a residual */
    cfg->clubMinMps = 10.0F;
    cfg->clubMaxMps = 70.0F;
    cfg->clubOutMaxRatio = 1.10F; /* after impact the club only slows */
    cfg->ballMinMps = 15.0F;
    cfg->ballMaxMps = 90.0F;
    cfg->gateSigmas = 3.0F;
    cfg->minSigmaUs = 500.0F;
}

void l3_impact_fit_reset(l3_impact_fit_t *fit)
{
    uint32_t i;

    memset(fit, 0, sizeof(*fit));
    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        fit->track[i].why = L3_FIT_WHY_MISSING;
    }
    fit->droppedTrack = L3_FIT_NO_TRACK;
}

int32_t l3_fit_list_point(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    const l3_fit_list_t *list = (const l3_fit_list_t *)ctx;

    if (index >= list->count) {
        return 0;
    }
    *out = list->points[index];
    return 1;
}

int32_t l3_fit_span_point(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    const l3_fit_span_t *span = (const l3_fit_span_t *)ctx;

    if (index >= span->count) {
        return 0;
    }
    return l3_track_point(span->track, span->first + index, out);
}

void l3_fit_span_after(const l3_club_track_t *track, uint32_t afterFrame, l3_fit_span_t *out)
{
    l3_track_point_t point;
    uint32_t i;

    out->track = track;
    out->first = track->count;
    out->count = 0U;
    for (i = 0U; i < track->count; i++) {
        (void)l3_track_point(track, i, &point);
        if (point.frame > afterFrame) {
            out->first = i;
            out->count = track->count - i;
            return;
        }
    }
}

/* This track's speed bounds; club out has no floor beyond moving downrange. */
static void l3_fit_bounds(const l3_impact_fit_cfg_t *cfg, uint8_t which, float *lo, float *hi)
{
    if (which == L3_FIT_BALL_OUT) {
        *lo = cfg->ballMinMps;
        *hi = cfg->ballMaxMps;
    } else if (which == L3_FIT_CLUB_IN) {
        *lo = cfg->clubMinMps;
        *hi = cfg->clubMaxMps;
    } else {
        *lo = 0.0F;
        *hi = cfg->clubMaxMps;
    }
}

void l3_impact_fit_track(const l3_impact_fit_cfg_t *cfg, uint8_t which, l3_point_at_fn pointAt,
                         const void *ctx, uint32_t count, float ballRangeM,
                         l3_fit_estimate_t *out)
{
    float t[L3_FIT_MAX_POINTS];
    float r[L3_FIT_MAX_POINTS];
    uint32_t want = (cfg->fitPoints < L3_FIT_MAX_POINTS) ? cfg->fitPoints : L3_FIT_MAX_POINTS;
    uint32_t n;
    uint32_t first;
    uint32_t i;
    float t0;
    float tMean = 0.0F;
    float rMean = 0.0F;
    float stt = 0.0F;
    float str = 0.0F;
    float rss = 0.0F;
    float v;
    float lo;
    float hi;
    float tk;
    float se;
    float floorM;
    l3_track_point_t point;

    memset(out, 0, sizeof(*out));
    out->why = L3_FIT_WHY_MISSING;
    if (pointAt == NULL || count == 0U) {
        return;
    }
    n = (count < want) ? count : want;
    first = (which == L3_FIT_CLUB_IN) ? count - n : 0U;
    out->points = n;
    if (n < 3U || n < cfg->minPoints) {
        out->why = L3_FIT_WHY_FEW_POINTS;
        return;
    }
    (void)pointAt(ctx, first, &point);
    t0 = (float)point.timestampUs;
    for (i = 0U; i < n; i++) {
        (void)pointAt(ctx, first + i, &point);
        t[i] = ((float)point.timestampUs - t0) * 1.0e-6F;
        r[i] = point.rangeM;
        tMean += t[i];
        rMean += r[i];
    }
    tMean /= (float)n;
    rMean /= (float)n;
    for (i = 0U; i < n; i++) {
        float dt = t[i] - tMean;

        stt += dt * dt;
        str += dt * (r[i] - rMean);
    }
    if (!(stt > 0.0F)) {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    v = str / stt;
    for (i = 0U; i < n; i++) {
        float e = r[i] - (rMean + v * (t[i] - tMean));

        rss += e * e;
    }
    out->speedMps = v;
    if (!isfinite(v) || !isfinite(rss)) {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    if (!(v > 0.0F)) {
        out->why = L3_FIT_WHY_WRONG_DIRECTION;
        return;
    }
    l3_fit_bounds(cfg, which, &lo, &hi);
    if (v < lo || v > hi) {
        out->why = L3_FIT_WHY_SPEED_BOUNDS;
        return;
    }
    tk = tMean + (ballRangeM - rMean) / v;
    se = sqrtf(rss / (float)(n - 2U)) *
         sqrtf(1.0F / (float)n + (tk - tMean) * (tk - tMean) / stt);
    floorM = cfg->binWidthM / sqrtf(12.0F);
    if (se < floorM) {
        se = floorM;
    }
    out->timeUs = t0 + tk * 1.0e6F;
    out->sigmaUs = se / v * 1.0e6F;
    if (!isfinite(out->timeUs) || !isfinite(out->sigmaUs)) {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    out->why = L3_FIT_WHY_OK;
}
