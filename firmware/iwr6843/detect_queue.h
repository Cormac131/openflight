/* Completed-slot queue for the live self-trigger.
 *
 * The HWA/EDMA writer publishes a pre-trigger ring slot only after that
 * slot's samples are stored. l3_detectTask pops the slot and runs the
 * leave detector while a later slot is captured. One producer, one consumer.
 * Python stays out of this path; tests/test_iwr6843_detect_queue.py builds
 * this file with the host C compiler.
 */
#ifndef L3_DETECT_QUEUE_H
#define L3_DETECT_QUEUE_H

#include <stdint.h>

#define L3_DETECT_QUEUE_DEPTH 64U

typedef struct {
    volatile uint16_t slot[L3_DETECT_QUEUE_DEPTH];
    volatile uint32_t epoch[L3_DETECT_QUEUE_DEPTH];
    volatile uint32_t stamp[L3_DETECT_QUEUE_DEPTH]; /* cycle count when published */
    volatile uint32_t head;
    volatile uint32_t tail;
    volatile uint32_t dropped;
    volatile uint32_t published;
} L3DetectQueue;

void l3detect_init(L3DetectQueue *queue);

/* Returns 0 when queued. Returns -1 and increments dropped when full. stamp
 * is the cycle count the frame was acquired at (l3_timing.h). */
int32_t l3detect_publish(L3DetectQueue *queue, uint16_t slot, uint32_t epoch, uint32_t stamp);

/* Returns 1 and writes the oldest item, or 0 when empty. */
int32_t l3detect_pop(L3DetectQueue *queue, uint16_t *slot, uint32_t *epoch, uint32_t *stamp);

/* Items waiting to be popped. */
uint32_t l3detect_depth(const L3DetectQueue *queue);

/* A slot stays readable until the writer is one frame from reusing it.
 * ringFrames < 2 cannot overlap a read with the next write. Checked when
 * the slot is popped AND again once it has been read: the writer may have
 * reached it meanwhile (a slow read, a DSS that answered late), and what
 * was read is then two frames mixed. The writer only moves forward, so a
 * slot live after the read was live throughout it. */
int32_t l3detect_slot_live(uint32_t epoch, uint32_t current, uint32_t ringFrames);

#endif /* L3_DETECT_QUEUE_H */
