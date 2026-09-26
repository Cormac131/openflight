"""Test doubles for the IWR6843 runtime and the l3sparse firmware.

``FakeIWRRuntime`` stands in for ``IWR6843Runtime`` in server tests and
``FakeCaptureMonitor`` for ``IWR6843CaptureMonitor``.
``test_iwr6843_fakes.py`` checks every public member here still exists on the
real class with the same parameters, so the double cannot drift silently.

The firmware helpers encode ILP1/ILS1 packets the way ``l3_cli_sparse`` does,
including its reserved noise field of 0.0, so driver tests exercise the real
byte layout rather than a host-only variant.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace
from typing import Callable

import numpy as np

from openflight.iwr6843.monitor import SelfTriggerConfig
from openflight.iwr6843.sparse import POWER_MAGIC, SLICE_MAGIC, PowerSummary
from openflight.iwr6843.tracking import Geometry, mti_filter

_POWER_HEADER = struct.Struct("<4sHHHHHHHf")
_SLICE_HEADER = struct.Struct("<4sH")
_CELL = struct.Struct("<HH")


class FakeIWRRuntime:
    """Minimal ``IWR6843Runtime`` for server tests.

    Pass ``process_shot`` to control the capture result; everything else
    behaves like a net-mode runtime with the sound-gate trigger.
    """

    self_trigger_enabled = False

    def __init__(
        self,
        *,
        process_shot: Callable | None = None,
        calibration=None,
    ):
        self._process_shot = process_shot
        self.calibration = calibration or SimpleNamespace(
            tee_range_m=1.575,
            radar_height_m=0.15875,
            tee_ball_height_m=0.04,
        )
        self.late_window_requests: list[dict] = []

    def process_shot(
        self,
        *,
        impact_timestamp,
        ball_speed_mph,
        club,
        club_speed_mph=None,
        tilt_deg=None,
    ):
        """Delegate to the ``process_shot`` callable given at construction."""
        if self._process_shot is None:
            return SimpleNamespace(capture=None, measurement=None, club_path=None)
        return self._process_shot(
            impact_timestamp=impact_timestamp,
            ball_speed_mph=ball_speed_mph,
            club=club,
            club_speed_mph=club_speed_mph,
            tilt_deg=tilt_deg,
        )

    def plan_late_window(self, *, ball_speed_mph, launch_angle_deg, spin_rpm):
        """Net mode: no late window."""
        del ball_speed_mph, launch_angle_deg, spin_rpm

    def measure_late_window(self, plan, *, impact_timestamp, on_measured):
        """Record the request; tests call ``on_measured`` themselves."""
        self.late_window_requests.append(
            {"plan": plan, "impact_timestamp": impact_timestamp, "on_measured": on_measured}
        )
        return True

    def stop(self):
        """Nothing to release."""


class FakeCaptureMonitor:
    """Minimal ``IWR6843CaptureMonitor`` for server tests.

    ``init_iwr6843`` reads the trigger surface straight off the monitor it
    builds, so this double mirrors it rather than approximating it:
    ``self_trigger`` holds the config and is ``None`` when the feature is off,
    and ``watch_self_trigger`` derives from it exactly as on the real class.

    Construction keywords are recorded in ``kwargs`` and the ``armed`` flag
    ``start`` was given in ``started_armed``, so tests can assert on how the
    server wired the monitor up.
    """

    def __init__(
        self,
        *,
        port: str = "/dev/ttyUSB0",
        radar=None,
        self_trigger: SelfTriggerConfig | None = None,
        slice_planner=None,
        onboard_tracking: bool = False,
        trigger_observers: list[Callable[[float], None]] | None = None,
        **other,
    ):
        self.port = port
        self.radar = radar
        self.self_trigger = self_trigger
        self.slice_planner = slice_planner
        self.onboard_tracking = onboard_tracking
        self._trigger_observers = list(trigger_observers or [])
        self.started_armed: bool | None = None
        self.armed = False
        self.stopped = False
        self.kwargs = {
            "port": port,
            "radar": radar,
            "self_trigger": self_trigger,
            "slice_planner": slice_planner,
            "onboard_tracking": onboard_tracking,
            "trigger_observers": trigger_observers,
            **other,
        }

    @property
    def watch_self_trigger(self) -> bool:
        """True when the firmware trigger replaces the GPIO edge."""
        return self.self_trigger is not None

    def start(self, *, armed: bool = True) -> None:
        """Record the arming decision; no hardware is touched."""
        self.started_armed = armed

    def arm(self) -> None:
        """Accept trigger edges."""
        self.armed = True

    def add_trigger_observer(self, observer: Callable[[float], None]) -> None:
        """Register a trigger observer the way the real monitor does."""
        self._trigger_observers.append(observer)

    def stop(self) -> None:
        """Nothing to release."""
        self.stopped = True


def capture_monitor_factory(**defaults):
    """Return ``(monitors, factory)`` for patching ``IWR6843CaptureMonitor``.

    Patch ``factory`` over the real class and read ``monitors`` afterwards: it
    collects every monitor the code under test built, in construction order.
    ``defaults`` supplies keywords the production caller does not pass, such as
    a fake ``radar``.
    """
    monitors: list[FakeCaptureMonitor] = []

    def factory(**kwargs) -> FakeCaptureMonitor:
        monitor = FakeCaptureMonitor(**{**defaults, **kwargs})
        monitors.append(monitor)
        return monitor

    return monitors, factory


def vertical_cube(cube: np.ndarray, n_tx: int) -> np.ndarray:
    """Keep the TX pair the vertical estimator uses (TX0 and TX2 of three)."""
    if n_tx == 2:
        return cube
    if n_tx != 3:
        raise ValueError(f"unsupported TX count {n_tx}")
    frames, chirps, n_rx, bins = cube.shape
    loops = chirps // n_tx
    tdm = cube.reshape(frames, loops, n_tx, n_rx, bins)
    return tdm[:, :, [0, 2]].reshape(frames, loops * 2, n_rx, bins)


def vertical_loop_power(
    cube: np.ndarray,
    *,
    n_tx: int,
    geometry: Geometry | None = None,
) -> PowerSummary:
    """What the firmware computes: burst-MTI residual power per loop and bin."""
    vertical = vertical_cube(cube, n_tx)
    mti = mti_filter(vertical, range_domain=True)
    power = (np.abs(mti) ** 2).sum(axis=(1, 3))
    power = power.reshape(power.shape[0] * power.shape[1], power.shape[-1])
    frames, chirps, n_rx, bins = vertical.shape
    if geometry is None:
        geometry = Geometry(
            n_frames=frames,
            chirps_per_frame=chirps,
            n_tx=2,
            n_rx=n_rx,
            n_samples=bins,
            frame_period_s=0.003,
            trigger_frame=0,
        )
    return PowerSummary(
        power=power.astype(np.float32),
        n_tx=n_tx,
        n_rx=n_rx,
        n_loops=chirps // 2,
        geometry=geometry,
    )


def power_packet(summary: PowerSummary) -> bytes:
    """ILP1 bytes exactly as ``l3_cli_sparse`` writes them (noise field 0.0)."""
    geometry = summary.geometry
    frames = geometry.n_frames
    header = _POWER_HEADER.pack(
        POWER_MAGIC,
        frames,
        summary.n_loops,
        summary.power.shape[-1],
        summary.n_tx,
        summary.n_rx,
        geometry.frame_bin_start(0) if frames else 0,
        int(round(geometry.frame_period_s * 1e6)),
        0.0,
    )
    table = bytearray()
    for frame in range(frames):
        table.append(geometry.frame_bin_start(frame) & 0xFF)
        table.append(geometry.frame_bin_count(frame) & 0xFF)
    return header + bytes(table) + np.ascontiguousarray(summary.power, dtype="<f4").tobytes()


def slice_packet(cube: np.ndarray, n_tx: int, cells: list[tuple[int, int]]) -> bytes:
    """ILS1 bytes for ``cells``: imag then real int16, vertical chirps, all RX."""
    vertical = vertical_cube(cube, n_tx)
    body = bytearray()
    for frame, local in cells:
        body += _CELL.pack(frame, local)
        flat = np.asarray(vertical[frame, :, :, local]).reshape(-1)
        interleaved = np.empty(flat.size * 2, dtype="<i2")
        interleaved[0::2] = np.clip(np.round(flat.imag), -32768, 32767)
        interleaved[1::2] = np.clip(np.round(flat.real), -32768, 32767)
        body += interleaved.tobytes()
    return _SLICE_HEADER.pack(SLICE_MAGIC, len(cells)) + bytes(body)


def parse_cell_request(request: bytes) -> list[tuple[int, int]]:
    """Decode a ``cells N f b ...`` line the way the firmware reads it."""
    fields = request.decode("ascii").split()
    assert fields[0] == "cells"
    count = int(fields[1])
    values = [int(value) for value in fields[2:]]
    assert len(values) == 2 * count
    return list(zip(values[0::2], values[1::2]))


class FakeSparseSerial:
    """Serial port that plays one l3sparse exchange.

    ``before_power`` is CLI text ahead of the ILP1 packet (echo, errors).
    After the host writes its cell request, the reply is built from the cells
    it actually asked for, followed by ``trailer``.
    """

    def __init__(
        self,
        *,
        cube: np.ndarray | None,
        n_tx: int,
        summary: PowerSummary | None,
        before_power: bytes = b"l3sparse\n",
        after_request: bytes | None = None,
        trailer: bytes = b"Done\n",
        chunk: int = 4096,
    ):
        self._cube = cube
        self._n_tx = n_tx
        self._chunk = chunk
        self._trailer = trailer
        self._after_request = after_request
        self._buffer = bytearray(before_power)
        if summary is not None:
            self._buffer += power_packet(summary)
        self.written: list[bytes] = []
        self.requested_cells: list[tuple[int, int]] | None = None

    @property
    def in_waiting(self) -> int:
        return min(len(self._buffer), self._chunk)

    def read(self, count: int) -> bytes:
        count = min(count, len(self._buffer))
        chunk = bytes(self._buffer[:count])
        del self._buffer[:count]
        return chunk

    def write(self, data: bytes) -> None:
        self.written.append(data)
        if not data.startswith(b"cells"):
            return
        if self._after_request is not None:
            self._buffer += self._after_request
            return
        self.requested_cells = parse_cell_request(data)
        self._buffer += slice_packet(self._cube, self._n_tx, self.requested_cells)
        self._buffer += self._trailer

    def reset_input_buffer(self) -> None:
        return None
