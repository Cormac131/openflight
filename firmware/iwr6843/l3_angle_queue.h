/* IWR6843 club angles off the decision path: the pending-angle queue.
 *
 * The fire decision uses the club track's range only; its angles feed the
 * delivery fit (path, attack) and the shot. So the detect task no longer
 * estimates them: it queues the appended point's channel snapshot (taken
 * while its ring slot is still valid) keyed by the point's timestamp, and a
 * low-priority angle task estimates it in spare time and sets the angles on
 * the point with that timestamp. A fire frame drains the queue itself,
 * after the freeze request, so the shot freezes every angle.
 *
 * The queue is FIFO; a full one drops its oldest job (the delivery fit reads
 * the newest points). Keyed by timestamp, a job finds its point however the
 * track moved on, or finds it gone (a reset, rolled off) and is stale. The
 * board serialises the two tasks (Task_disable around pop and apply's
 * write); the module itself does no locking. Pure C, no hardware.
 */
#ifndef L3_ANGLE_QUEUE_H
#define L3_ANGLE_QUEUE_H

#include <stdint.h>

#include "l3_angle.h"
#include "l3_club_track.h"

#define L3_ANGLE_QUEUE_DEPTH 12U

typedef struct {
    uint32_t timestampUs;          /* the track point's */
    l3_angle_snapshot_t snapshot;  /* its channels, taken on the detect task */
} l3_angle_job_t;

typedef struct {
    l3_angle_job_t jobs[L3_ANGLE_QUEUE_DEPTH];
    uint32_t head;     /* the oldest job */
    uint32_t count;
    uint32_t queued;   /* pushed, ever */
    uint32_t done;     /* applied to their point */
    uint32_t stale;    /* their point was gone */
    uint32_t failed;   /* the estimator refused the snapshot */
    uint32_t dropped;  /* pushed out of a full queue */
} l3_angle_queue_t;

uint32_t l3_angle_queue_size(void);
uint32_t l3_angle_job_size(void);
void l3_angle_queue_init(l3_angle_queue_t *queue);
/* Queue a job: 1, or 0 when a full queue dropped its oldest to make room. */
int32_t l3_angle_queue_push(l3_angle_queue_t *queue, uint32_t timestampUs,
                            const l3_angle_snapshot_t *snapshot);
/* The oldest job into out: 1, or 0 when empty. */
int32_t l3_angle_queue_pop(l3_angle_queue_t *queue, l3_angle_job_t *out);
uint32_t l3_angle_queue_pending(const l3_angle_queue_t *queue);
/* The oldest job copied into out, left queued: 1, or 0 when empty. The
 * angle task's first step: it estimates outside the lock, then finishes. */
int32_t l3_angle_queue_peek(const l3_angle_queue_t *queue, l3_angle_job_t *out);
/* The angle task's last step, under the lock: when the job it peeked is
 * still the oldest, take it and record its estimate (estimated: what
 * l3_angle_estimate returned; obs its output) as l3_angle_queue_apply
 * does, returning 1 / 0 / -1 alike. -2 when the job is no longer the
 * oldest (a fire frame's drain applied it meanwhile): nothing is done. */
int32_t l3_angle_queue_finish(l3_angle_queue_t *queue, const l3_angle_job_t *job,
                              int32_t estimated, const l3_angle_obs_t *obs,
                              l3_club_track_t *track);
/* Estimate a job (obs receives the estimate) and set its angles on the
 * track point with its timestamp: 1 applied, 0 the estimator refused the
 * snapshot (nothing set), -1 no point with that timestamp (stale, nothing
 * set). Counted in the queue's done / failed / stale. */
int32_t l3_angle_queue_apply(l3_angle_queue_t *queue, const l3_radar_cal_t *cal,
                             const l3_angle_job_t *job, l3_club_track_t *track,
                             l3_angle_obs_t *obs);

#endif /* L3_ANGLE_QUEUE_H */
