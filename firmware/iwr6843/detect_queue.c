#include "detect_queue.h"

void l3detect_init(L3DetectQueue *queue)
{
    uint32_t index;

    for (index = 0U; index < L3_DETECT_QUEUE_DEPTH; index++) {
        queue->slot[index] = 0U;
        queue->epoch[index] = 0U;
    }
    queue->head = 0U;
    queue->tail = 0U;
    queue->dropped = 0U;
    queue->published = 0U;
}

int32_t l3detect_publish(L3DetectQueue *queue, uint16_t slot, uint32_t epoch)
{
    uint32_t tail = queue->tail;
    uint32_t held = tail - queue->head;

    if (held >= L3_DETECT_QUEUE_DEPTH) {
        queue->dropped++;
        return -1;
    }
    queue->slot[tail % L3_DETECT_QUEUE_DEPTH] = slot;
    queue->epoch[tail % L3_DETECT_QUEUE_DEPTH] = epoch;
    queue->published++;
    queue->tail = tail + 1U;
    return 0;
}

int32_t l3detect_pop(L3DetectQueue *queue, uint16_t *slot, uint32_t *epoch)
{
    uint32_t head = queue->head;

    if (head == queue->tail) {
        return 0;
    }
    *slot = queue->slot[head % L3_DETECT_QUEUE_DEPTH];
    *epoch = queue->epoch[head % L3_DETECT_QUEUE_DEPTH];
    queue->head = head + 1U;
    return 1;
}

int32_t l3detect_slot_live(uint32_t epoch, uint32_t current, uint32_t ringFrames)
{
    if (ringFrames < 2U || current < epoch) {
        return 0;
    }
    return ((current - epoch) < (ringFrames - 1U)) ? 1 : 0;
}
