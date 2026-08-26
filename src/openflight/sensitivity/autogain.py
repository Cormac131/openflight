"""Closed-loop gain control from the measured envelope peak.

The detector's `GATE` output only says "loud enough"; its `ENVELOPE` output says
*how* loud, and that is what lets the gain tune itself. After each shot the
controller compares the recent envelope peaks against a target band and nudges
the digipot between shots — never during one.

Three things keep it from chasing its own tail:

* **It decides on a median of the last few shots**, not the last one. A thinned
  strike or a fat one should not move the gain on its own.
* **It clears that history whenever it moves the wiper.** Peaks measured at the
  old gain say nothing about the new one, and averaging across a change is how
  a loop like this oscillates.
* **It steps part of the way, not all of it.** The envelope is only
  approximately linear in gain, so a full-correction jump would overshoot.

**What this can and cannot fix.** R17 works against the board's fixed 100 kOhm
R3, so a 10 kOhm pot moves the preamp leg by only ~1.2x across its whole travel
— a trim, not a wide-range AGC. It tracks room and placement drift and keeps the
gain centred in the window the *series resistor* chose; it cannot rescue a
detector that is too far from the ball or a series resistor that is simply
wrong. When the loop runs out of travel it says so, and says to change that
resistor, rather than sitting at an end stop pretending to work.

The one exception to the median is clipping. A clipped peak is unambiguous — the
preamp railed, and the measured fraction understates by an unknown amount — so
it is acted on immediately rather than costing three more spoiled captures.
"""

from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .ds3502 import DEFAULT_SERIES_OHMS, MAX_POSITION, POSITION_COUNT, preamp_feedback_ohms

logger = logging.getLogger(__name__)

# Target band as a fraction of the detector's own supply, which is where it
# clips. Below this the capture is needlessly quiet; above it there is no
# headroom for a harder strike.
DEFAULT_TARGET_LOW = 0.60
DEFAULT_TARGET_HIGH = 0.80

# Shots to see before acting on a median, and how many to keep.
DEFAULT_MIN_SHOTS = 3
DEFAULT_HISTORY = 5

# Largest single correction, in wiper steps. Bounds how wrong one bad decision
# can be, and keeps the loop visibly convergent rather than jumpy.
DEFAULT_MAX_STEP = 10

# Fraction of the modelled correction to actually apply.
DEFAULT_DAMPING = 0.6

# Consecutive in-band shots before the settled value is committed to EEPROM.
# Auto adjustments are otherwise volatile: at a write per shot the part's
# endurance would not survive a season.
DEFAULT_COMMIT_AFTER_STABLE = 10

WAITING = "waiting"
HOLD = "hold"
RAISE = "raise"
LOWER = "lower"
AT_LIMIT = "at_limit"


@dataclass(frozen=True)
class AutoGainDecision:
    """What the controller concluded from one shot."""

    action: str
    position: int
    next_position: int
    reason: str
    commit: bool = False
    median_fraction: Optional[float] = None
    shots_considered: int = 0

    @property
    def changed(self) -> bool:
        """True when the wiper should actually move."""
        return self.next_position != self.position

    def to_dict(self) -> dict:
        """Return the WebSocket payload for this decision."""
        return {
            "action": self.action,
            "position": self.position,
            "next_position": self.next_position,
            "reason": self.reason,
            "committed": self.commit,
            "median_fraction": (
                round(self.median_fraction, 4) if self.median_fraction is not None else None
            ),
            "shots_considered": self.shots_considered,
        }


def achievable_gain_range(series_ohms: float = DEFAULT_SERIES_OHMS) -> float:
    """Return the gain ratio between the wiper's two end stops.

    This is the loop's entire authority. R17 works against the board's fixed
    100 kOhm R3, so a 10 kOhm pot behind a large series resistor moves the
    preamp leg very little — 33 kOhm gives about 1.21x end to end.
    """
    return preamp_feedback_ohms(MAX_POSITION, series_ohms) / preamp_feedback_ohms(0, series_ohms)


def band_ratio(target_low: float, target_high: float) -> float:
    """Return how wide a target band is, as a ratio."""
    return target_high / target_low


def has_authority(target_low: float, target_high: float, series_ohms: float) -> bool:
    """True when the loop can actually move a peak across its target band.

    A band wider than the achievable gain range is inert: once a peak is inside
    it, no reachable wiper step can push it out, so the loop holds forever and
    looks broken when it is merely out of travel.
    """
    return achievable_gain_range(series_ohms) > band_ratio(target_low, target_high)


def position_for_gain_ratio(
    position: int, ratio: float, series_ohms: float = DEFAULT_SERIES_OHMS
) -> int:
    """Return the step whose preamp resistance is ``ratio`` times this one's.

    Envelope amplitude tracks preamp gain, which tracks the feedback
    resistance, so a desired change in peak maps to a desired resistance. The
    resistance is not linear in wiper step, so this searches the 128 steps
    rather than scaling the index — cheap, and exact against the same model the
    UI reports.
    """
    desired = preamp_feedback_ohms(position, series_ohms) * ratio
    return min(
        range(POSITION_COUNT),
        key=lambda step: abs(preamp_feedback_ohms(step, series_ohms) - desired),
    )


class AutoGainController:
    """Decide wiper moves from observed envelope peaks."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        series_ohms: float = DEFAULT_SERIES_OHMS,
        target_low: float = DEFAULT_TARGET_LOW,
        target_high: float = DEFAULT_TARGET_HIGH,
        min_shots: int = DEFAULT_MIN_SHOTS,
        history: int = DEFAULT_HISTORY,
        max_step: int = DEFAULT_MAX_STEP,
        damping: float = DEFAULT_DAMPING,
        commit_after_stable: int = DEFAULT_COMMIT_AFTER_STABLE,
    ):
        if not 0.0 < target_low < target_high < 1.0:
            raise ValueError("target band must satisfy 0 < low < high < 1")
        if min_shots < 1 or history < min_shots:
            raise ValueError("history must be at least min_shots, and min_shots at least 1")
        if max_step < 1:
            raise ValueError("max_step must be at least 1")
        if not 0.0 < damping <= 1.0:
            raise ValueError("damping must be within (0, 1]")
        self.series_ohms = series_ohms
        if not has_authority(target_low, target_high, series_ohms):
            logger.warning(
                "[SENSITIVITY] Auto gain has little authority: the %.0f%%-%.0f%% band spans "
                "%.2fx but a %.0f ohm series resistor only allows %.2fx end to end. The loop "
                "will mostly hold. Narrow the band, or fit a smaller series resistor.",
                target_low * 100,
                target_high * 100,
                band_ratio(target_low, target_high),
                series_ohms,
                achievable_gain_range(series_ohms),
            )
        self.target_low = target_low
        self.target_high = target_high
        self.min_shots = min_shots
        self.max_step = max_step
        self.damping = damping
        self.commit_after_stable = commit_after_stable
        self._peaks: deque = deque(maxlen=history)
        self._stable_shots = 0
        self._committed = True

    @property
    def target_centre(self) -> float:
        """Middle of the target band, which corrections aim at."""
        return (self.target_low + self.target_high) / 2.0

    def reset(self) -> None:
        """Forget observed peaks. Called when the wiper moves for any reason."""
        self._peaks.clear()
        self._stable_shots = 0

    def observe(self, fraction: float, position: int, *, clipped: bool = False):
        """Fold one shot's envelope peak in and return the resulting decision.

        Args:
            fraction: Peak as a share of the detector's supply, 0..1+.
            position: The wiper step that peak was measured at.
            clipped: The peak reached the rail, so ``fraction`` is a floor
                rather than a measurement.
        """
        if clipped:
            # No point averaging: the preamp railed, so the only question is
            # how far down to go, and the answer is "decisively".
            self.reset()
            self._committed = False
            target = max(0, position - self.max_step)
            if target == position:
                return AutoGainDecision(
                    action=AT_LIMIT,
                    position=position,
                    next_position=position,
                    reason="Envelope is clipping at the least sensitive setting; "
                    "gain is not the limit here.",
                    shots_considered=1,
                )
            return AutoGainDecision(
                action=LOWER,
                position=position,
                next_position=target,
                reason=f"Envelope clipped; dropping {position - target} steps immediately.",
                shots_considered=1,
            )

        self._peaks.append(fraction)
        if len(self._peaks) < self.min_shots:
            return AutoGainDecision(
                action=WAITING,
                position=position,
                next_position=position,
                reason=(
                    f"Collecting shots ({len(self._peaks)}/{self.min_shots}) before adjusting."
                ),
                shots_considered=len(self._peaks),
            )

        median = statistics.median(self._peaks)
        considered = len(self._peaks)

        if self.target_low <= median <= self.target_high:
            self._stable_shots += 1
            commit = (
                not self._committed
                and self.commit_after_stable > 0
                and self._stable_shots >= self.commit_after_stable
            )
            if commit:
                self._committed = True
            return AutoGainDecision(
                action=HOLD,
                position=position,
                next_position=position,
                reason=f"Median peak {median:.0%} is inside the {self._band_text()} band.",
                commit=commit,
                median_fraction=median,
                shots_considered=considered,
            )

        modelled = position_for_gain_ratio(position, self.target_centre / median, self.series_ohms)
        target = self._damped_target(position, modelled)
        direction = RAISE if target > position else LOWER

        if target == position:
            at_end = (position == 0 and median > self.target_high) or (
                position == MAX_POSITION and median < self.target_low
            )
            if at_end:
                self.reset()
                return AutoGainDecision(
                    action=AT_LIMIT,
                    position=position,
                    next_position=position,
                    reason=(
                        f"Median peak {median:.0%} is outside the band but the wiper is at "
                        f"step {position}. Change the series resistor to move the range."
                    ),
                    median_fraction=median,
                    shots_considered=considered,
                )
            return AutoGainDecision(
                action=HOLD,
                position=position,
                next_position=position,
                reason=f"Median peak {median:.0%} is off-band but within one step.",
                median_fraction=median,
                shots_considered=considered,
            )

        # Peaks gathered at the old gain say nothing about the new one.
        self.reset()
        self._committed = False
        return AutoGainDecision(
            action=direction,
            position=position,
            next_position=target,
            reason=(
                f"Median peak {median:.0%} is {'below' if direction == RAISE else 'above'} the "
                f"{self._band_text()} band; moving {abs(target - position)} steps."
            ),
            median_fraction=median,
            shots_considered=considered,
        )

    def _band_text(self) -> str:
        return f"{self.target_low:.0%}-{self.target_high:.0%}"

    def _damped_target(self, position: int, modelled: int) -> int:
        step = modelled - position
        if step == 0:
            return position
        damped = int(round(step * self.damping))
        if damped == 0:
            # Never stall on rounding: a correction worth making is worth one step.
            damped = 1 if step > 0 else -1
        damped = max(-self.max_step, min(self.max_step, damped))
        return max(0, min(MAX_POSITION, position + damped))
