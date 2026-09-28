"""Target lists for the two tracks after impact: the club carrying on and the ball.

The ball-hypothesis and ball-track unit tests feed these straight to the C
(no radar cube): per post-impact frame, the extracted targets as
``l3_obs_extract`` lists them (strongest first) and the index of the one the
club track claimed. Ranges are global bins; Doppler is aliased onto
[-span/2, span/2) as the observation layer reads it (positive = receding).
Before impact the ball is stationary and burst MTI cancels it, so it is not
listed; the club approaches the origin.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openflight.iwr6843 import firmware_host as fw

BIN_M = 6.0 / 128
SPAN_MPS = 2 * fw.OBS_WAVELENGTH_M / (4 * 135e-6)
NO_CLAIM = fw.TRACK_NO_TARGET


def alias(speed_mps: float, span: float = SPAN_MPS) -> float:
    """A radial speed as the lag-1 Doppler reads it."""
    return ((speed_mps + span / 2) % span) - span / 2


def obs(
    frame: int, timestamp_us: int, range_bin: float, stat: float, speed_mps: float
) -> fw.TargetObs:
    t = fw.TargetObs()
    t.frame = frame
    t.timestampUs = timestamp_us
    t.peakBin = int(round(range_bin))
    t.rangeBin = range_bin
    t.energy = 4.0 * stat
    t.peak = stat
    t.stat = stat
    t.snr = stat / 100.0
    t.coherence = 0.9
    t.dopplerAliasMps = alias(speed_mps)
    t.confidence = 0.9
    return t


@dataclass
class Frame:
    frame: int
    timestamp_us: int
    targets: list
    club_index: int
    ball_bin: float | None  # the ball's own listed return; None when not listed


@dataclass
class TwoTracks:
    """A club and a ball leaving the origin; every field is one knob of the scene."""

    origin_bin: float = 46.0
    gate_us: int = 0  # when the tracker is armed (the gate fired)
    impact_offset_us: int = 0  # the true impact is gate_us + impact_offset_us
    club_mps: float = 28.0
    club_decel_mps2: float = 1500.0
    ball_mps: float = 42.0
    club_stat: float = 9000.0
    ball_stat: float = 1500.0
    frame_us: int = 2000
    frames: int = 8
    timestamps_us: list | None = None  # explicit post-frame times, for uneven spacing
    missing_ball: tuple = ()  # 1-based post frames without a ball return
    merged: tuple = ()  # post frames where club and ball are one, club-claimed return
    club_visible: bool = True
    extras: list = field(default_factory=list)  # (range bin, stat, speed m/s) returns

    def _club(self, s: float) -> tuple[float, float]:
        if s < 0.0:
            return self.origin_bin + self.club_mps * s / BIN_M, self.club_mps
        stop_s = self.club_mps / self.club_decel_mps2 if self.club_decel_mps2 > 0 else 1e9
        t = min(s, stop_s)
        travelled = self.club_mps * t - 0.5 * self.club_decel_mps2 * t * t
        return self.origin_bin + travelled / BIN_M, max(
            self.club_mps - self.club_decel_mps2 * t, 0.0
        )

    def build(self) -> list[Frame]:
        out = []
        for k in range(1, self.frames + 1):
            ts = (
                self.timestamps_us[k - 1]
                if self.timestamps_us
                else self.gate_us + k * self.frame_us
            )
            s = (ts - self.gate_us - self.impact_offset_us) * 1e-6
            entries = []  # (bin, stat, speed, kind)
            club_bin, club_speed = self._club(s)
            ball_listed = s > 0.0 and k not in self.missing_ball
            ball_bin = self.origin_bin + self.ball_mps * s / BIN_M if s > 0.0 else None
            if self.club_visible:
                if k in self.merged and ball_listed:
                    entries.append(
                        (0.5 * (club_bin + ball_bin), self.club_stat, club_speed, "club")
                    )
                    ball_listed = False
                else:
                    entries.append((club_bin, self.club_stat, club_speed, "club"))
            if ball_listed:
                entries.append((ball_bin, self.ball_stat, self.ball_mps, "ball"))
            for bin_, stat, speed in self.extras:
                entries.append((bin_, stat, speed, "extra"))
            entries.sort(key=lambda e: -e[1])  # strongest first, as l3_obs_extract lists them
            targets = [obs(k, ts, b, st, sp) for b, st, sp, _ in entries]
            kinds = [kind for *_, kind in entries]
            club_index = kinds.index("club") if "club" in kinds else NO_CLAIM
            out.append(Frame(k, ts, targets, club_index, ball_bin if ball_listed else None))
        return out
