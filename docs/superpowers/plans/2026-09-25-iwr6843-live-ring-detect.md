# IWR6843 Live Ring Detection

**Goal:** Keep capturing into the L3 ring while the leave detector reads a finished pre-trigger slot.

**Architecture:** The hardware accelerator remains the writer. IQ16 publishes the slot from the output-done callback. IQ8 publishes it from the pack-done callback, after the compact samples are in the ring. `l3_detectTask` runs below the rearm task, so the next frame is already being captured when the detector runs. A slot is stale once the writer is one frame from reusing it. The launch-angle fit stays on the Pi.

**Not in this change:** The C674x DSP image. The firmware build is MSS/R4F only, and LCMF still needs the OPS speed, calibration, and inclinometer reading that live on the Pi.

## Contract

- `firmware/iwr6843/detect_queue.c` is host-compiled C. Publish preserves order, a full queue drops the new slot, and `l3detect_slot_live` rejects a slot the ring is about to reuse.
- `l3_hwaRearmTask` does not call `l3_considerSelfTrigger`.
- `l3_detectTask` calls it with the queued slot after the live check.
- `stats` prints `detect dropped=<n> stale=<n>` on its own line.
