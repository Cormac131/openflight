"""The self-trigger's freeze/release handshake, firmware/iwr6843/l3_dump.c.

2026-09-30, the acceptance run on the board: between the verify and the dss
phases the board locked up. Its timeline showed the frame right after a
rearm (slot 0, epoch 1) followed at once by a post-impact tail (slots 9-23),
no swing, then nothing: frozen, not latched, so nothing rearmed it.

The mechanism: every completed freeze posts gHwaFreezeSemaphore (binary).
l3release on a latched board waited on it only while the capture still
ran, so a ring that froze before the release left its post stale. At the
next fire a release arriving mid-tail found that stale post, stopped the
capture before the ISR reached the freeze boundary, and left
gHwaFreezeRequested set; the rearm did not clear it, so the first frame
after the rearm began a new post capture, which froze unlatched.

These pin the three fixes; the race itself cannot run on the host.
"""

from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "l3_dump.c"
DRAIN = "while (Semaphore_pend(gHwaFreezeSemaphore, BIOS_NO_WAIT))"


def _function(signature: str) -> str:
    text = SOURCE.read_text(encoding="utf-8")
    body = text[text.index(signature + "\n{") :]
    return body[: body.index("\n}\n")]


def test_a_fire_drains_stale_freeze_completions_before_requesting_its_freeze():
    trigger = _function("static void l3_considerSelfTrigger(uint32_t slot)")
    request = trigger.index("gHwaFreezeRequested = 1U;")
    assert DRAIN in trigger[:request]


def test_a_release_consumes_the_completion_of_a_ring_that_already_froze():
    wait = _function("static int32_t l3_awaitFrozenRing(void)")
    latched = wait[wait.index("if (gSelfTriggerLatched) {") :]
    latched = latched[: latched.index("return l3_finishCaptureStop();")]
    assert "else if (gHwaFreezeSemaphore != NULL)" in latched
    assert "(void)Semaphore_pend(gHwaFreezeSemaphore, BIOS_NO_WAIT);" in latched


def test_a_rearm_clears_any_pending_freeze_request_before_the_front_end_restarts():
    rearm = _function("static int32_t l3_sparseRearm(void)")
    clear = rearm.index("gHwaFreezeRequested = 0U;")
    assert clear < rearm.index("l3_startFrontEnd()")
    assert clear < rearm.index("l3_restartCompletedHwaFrame()")
