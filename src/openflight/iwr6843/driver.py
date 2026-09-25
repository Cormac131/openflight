"""IWR6843 serial driver — the host side of the L3-dump firmware contract.

Firmware v3+ speaks a SINGLE UART (the CP2105 Enhanced interface) at
1,041,667 baud for both CLI commands and the binary dump; the dump is framed
by its "ILD1" magic plus the header-declared length, so CLI echo and payload
can share the pipe. Hardware-validated 2026-07-13 at 100% of wire rate.

Gotchas baked in (each cost a debugging session):
- DTR/RTS must be held low on open (TI EVMs tie them to reset/boot mode).
- One serial handle only — two handles on one tty steal each other's bytes.
- The CP2105 can stall a stream for seconds (cp210x -110 control timeouts)
  and resume; the reader waits out gaps up to ``stall_tolerance_s``.
"""

from __future__ import annotations

import glob
import logging
import time

import serial

from openflight.iwr6843.dump import HEADER, MAGIC, parse_header, payload_nbytes
from openflight.iwr6843.sparse import (
    POWER_MAGIC,
    SLICE_MAGIC,
    TRACK_MAGIC,
    CaptureLayout,
    OnboardTrack,
    assemble_dump,
    format_cell_request,
    parse_power,
    parse_track,
    power_packet_size,
    slice_stride,
    track_packet_size,
)

BAUD = 1_041_667
_PORT_GLOBS = ("/dev/ttyUSB*", "/dev/tty.SLAB_USBtoUART*")

logger = logging.getLogger(__name__)


class UnsupportedCommand(RuntimeError):
    """The firmware CLI does not know the command, so it is an older image."""


def open_port(port: str, baud: int = BAUD, timeout: float = 0.3) -> serial.Serial:
    """DTR/RTS-safe serial open."""
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


class IWR6843Radar:
    """CLI + dump transport for the custom L3-dump firmware."""

    # Bytes read past the end of one sparse packet, kept for the next one.
    _backlog: bytes = b""

    def __init__(self, port: str | None = None, baud: int = BAUD):
        if port is None:
            port = self.detect_port(baud)
            if port is None:
                raise RuntimeError("no IWR6843 CLI found — board on, flashed, single-port fw?")
        self.port = port
        self.ser = open_port(port, baud)

    @staticmethod
    def detect_port(baud: int = BAUD) -> str | None:
        """First serial port whose CLI answers `help` with our commands."""
        candidates: list[str] = []
        for pattern in _PORT_GLOBS:
            candidates.extend(sorted(glob.glob(pattern)))
        for cand in candidates:
            try:
                ser = open_port(cand, baud)
            except (OSError, serial.SerialException):
                continue
            try:
                ser.reset_input_buffer()
                ser.write(b"help\n")
                resp = b""
                deadline = time.time() + 1.5
                while time.time() < deadline and b"sensorStart" not in resp:
                    resp += ser.read(512)
            finally:
                ser.close()
            if b"sensorStart" in resp:
                return cand
        return None

    def consume_trigger_notice(self, pending: bytes = b"") -> tuple[bool, bytes]:
        """Read any idle CLI bytes and report the self-trigger ``Triggered`` line."""
        waiting = self.ser.in_waiting
        if waiting:
            pending += self.ser.read(waiting)
        if b"Triggered" in pending:
            return True, b""
        if len(pending) > 64:
            pending = pending[-64:]
        return False, pending

    def cmd(self, line: str, window: float = 1.5) -> str:
        """Send one CLI line; collect the response until Done/Error/timeout."""
        self.ser.reset_input_buffer()
        self.ser.write((line + "\n").encode())
        resp = b""
        deadline = time.time() + window
        while time.time() < deadline:
            resp += self.ser.read(512)
            if b"Done" in resp or b"Error" in resp:
                break
        return resp.decode(errors="replace")

    def drain_stale_output(
        self,
        *,
        max_wait_s: float = 10.0,
        initial_quiet_s: float = 0.25,
        stream_quiet_s: float = 4.25,
    ) -> int:
        """Drain an abandoned binary dump before sending configuration commands.

        If a host process exits during ``l3dump``, the firmware can still be
        writing the old payload through the CP2105. Commands sent into that
        stream are not safe to associate with their responses. Once bytes are
        observed, tolerate the bridge's known multi-second stalls before
        declaring the stream quiet.
        """
        drained = 0
        saw_data = False
        start = time.monotonic()
        last_data = start
        while time.monotonic() - start < max_wait_s:
            waiting = self.ser.in_waiting
            if waiting:
                chunk = self.ser.read(min(waiting, 4096))
                if chunk:
                    drained += len(chunk)
                    saw_data = True
                    last_data = time.monotonic()
                    continue
            quiet_s = stream_quiet_s if saw_data else initial_quiet_s
            if time.monotonic() - last_data >= quiet_s:
                break
            time.sleep(0.01)
        if drained:
            logger.warning(
                "[IWR6843] Drained %d stale UART bytes before configuration",
                drained,
            )
        return drained

    @staticmethod
    def _require_done(command: str, response: str) -> None:
        if "Error" in response:
            raise RuntimeError(f"config rejected: {command!r}: {response.strip()}")
        if "Done" not in response:
            raise RuntimeError(
                f"IWR6843 did not acknowledge {command!r}; "
                "the firmware may be wedged (press RESET and retry)"
            )

    def send_config(self, cfg_path: str) -> None:
        """Stop and flush old state, then stream the cfg; raise on Error.

        The firmware's geometry guard rejects a cfg whose loops/samples don't
        match the flashed build — that surfaces here as RuntimeError.
        """
        self.drain_stale_output()
        self._require_done("sensorStop", self.cmd("sensorStop", 3.0))
        self._require_done("flushCfg", self.cmd("flushCfg", 1.5))
        with open(cfg_path, encoding="utf-8") as cfg:
            for rawline in cfg:
                line = rawline.strip()
                if not line or line.startswith("%"):
                    continue
                # The driver owns the lifecycle commands so every config gets
                # the required stop/flush ordering without sending duplicates.
                if line in {"sensorStop", "flushCfg"}:
                    continue
                window = 6.0 if line.startswith("sensorStart") else 1.5
                resp = self.cmd(line, window)
                self._require_done(line, resp)
        deadline = time.monotonic() + 6.0
        health = ""
        while time.monotonic() < deadline:
            health = self.stats()
            self._require_done("stats", health)
            if "active=1" in health:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"IWR6843 did not enter active capture mode: {health.strip()}")

    def read_dump(self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0) -> bytes:
        """Fire `l3dump` and return one complete dump (best effort on stalls).

        Syncs on the ILD1 magic past the CLI echo and sizes the read from the
        dump's own header, so any firmware geometry works.
        """
        self.ser.reset_input_buffer()
        self.ser.write(b"l3dump\n")
        buf = bytearray()
        expected: int | None = None
        start = time.time()
        last = start
        while time.time() - start < timeout_s:
            waiting = self.ser.in_waiting
            chunk = self.ser.read(waiting if waiting else 1)
            if chunk:
                buf.extend(chunk)
                last = time.time()
            elif buf and time.time() - last > stall_tolerance_s:
                break
            if expected is None:
                idx = buf.find(MAGIC)
                if idx >= 0 and len(buf) - idx >= HEADER.size:
                    del buf[:idx]
                    try:
                        metadata = parse_header(buf)
                        expected = metadata["header_nbytes"] + payload_nbytes(metadata, buf)
                    except ValueError:
                        expected = None
            elif len(buf) >= expected:
                break
        if expected is None:
            return bytes(buf)

        payload = bytes(buf[:expected])
        if len(payload) == expected:
            # The binary payload can finish just before the CLI handler returns.
            # Wait for its trailing Done before another command can be consumed
            # by the firmware while it is still completing dump/restart work.
            elapsed = time.time() - start
            trailer = self._wait_for_dump_cli_ready(
                buf[expected:], timeout_s=min(1.0, max(0.0, timeout_s - elapsed))
            )
            if b"Error" in trailer:
                raise RuntimeError(
                    f"IWR6843 dump completed but firmware restart failed: "
                    f"{trailer.decode(errors='replace').strip()}"
                )
        return payload

    def read_sparse(self, planner, timeout_s: float = 8.0):
        """Freeze, read residual power, then the complex cells ``planner`` names.

        Returns the assembled range-snapshot dump, or None when this firmware
        has no ``l3sparse`` command so the caller can fall back to ``l3dump``.
        """
        self.ser.reset_input_buffer()
        self._backlog = b""
        self.ser.write(b"l3sparse\n")
        try:
            packet = self._read_packet(POWER_MAGIC, power_packet_size, timeout_s)
        except UnsupportedCommand:
            return None
        if packet is None:
            return None
        summary = parse_power(packet)
        self.ser.write(format_cell_request(list(planner(summary))))
        raw = self._read_cells(summary, timeout_s)
        if raw is None:
            return None
        return raw, summary.noise_power

    def read_tracked(self, timeout_s: float = 8.0) -> tuple[bytes, float, OnboardTrack] | None:
        """Freeze and read the cells the firmware tracker chose (``l3track``).

        Returns the assembled dump, the noise power and the firmware's track,
        or None when the firmware refuses before streaming (no trackCfg, IQ8
        storage), so the caller can fall back to another command.
        Raises UnsupportedCommand when the firmware has no ``l3track``, and
        RuntimeError when the stream breaks after it starts: the ring has
        already been rearmed, so a fallback would capture the wrong window.
        """
        self.ser.reset_input_buffer()
        self._backlog = b""
        self.ser.write(b"l3track\n")
        head = self._read_magic(TRACK_MAGIC, track_packet_size(b""), timeout_s)
        if head is None:
            return None
        packet = self._complete(head, track_packet_size(head), timeout_s)
        if packet is None:
            raise RuntimeError("IWR6843 l3track packet ended early")
        layout, track = parse_track(packet)
        raw = self._read_cells(layout, timeout_s)
        if raw is None:
            raise RuntimeError("IWR6843 l3track cell packet ended early")
        return raw, layout.noise_power, track

    def _read_packet(self, magic: bytes, size_of, timeout_s: float) -> bytes | None:
        """Read one header-sized packet: sync on ``magic``, then its declared length."""
        packet = self._read_magic(magic, size_of(b""), timeout_s)
        if packet is None:
            return None
        return self._complete(packet, size_of(packet), timeout_s)

    def _complete(self, packet: bytes, total: int, timeout_s: float) -> bytes | None:
        """Read the rest of a ``total``-byte packet that starts with ``packet``.

        Bytes past ``total`` belong to the next packet (l3track streams ILS1
        straight after ILT1), so they wait in the backlog for the next read.
        """
        if len(packet) < total:
            rest = self._read_exact(total - len(packet), timeout_s)
            if rest is None:
                return None
            packet += rest
        self._backlog = packet[total:]
        return packet[:total]

    def _read_cells(self, layout: CaptureLayout, timeout_s: float) -> bytes | None:
        """Read one ILS1 cell packet and rebuild the dump it describes."""
        slice_packet = self._read_magic(SLICE_MAGIC, 6, timeout_s)
        if slice_packet is None:
            return None
        count = int.from_bytes(slice_packet[4:6], "little")
        slice_packet = self._complete(slice_packet, 6 + count * slice_stride(layout), timeout_s)
        if slice_packet is None:
            return None
        return assemble_dump(layout, slice_packet)

    def _read_magic(self, magic: bytes, minimum: int, timeout_s: float) -> bytes | None:
        """Read until ``magic``. None on a CLI error or timeout before it.

        Raises UnsupportedCommand when the CLI does not recognise the command.
        """
        buf = bytearray()
        chunk, self._backlog = self._backlog, b""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not chunk:
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting else 1)
            if not chunk:
                continue
            buf.extend(chunk)
            chunk = b""
            idx = buf.find(magic)
            # Only CLI text before the magic can be a reply; after it is binary.
            text = buf if idx < 0 else buf[:idx]
            if b"not recognized" in text:
                raise UnsupportedCommand(text.decode(errors="replace").strip())
            if b"Error" in text:
                return None
            if idx >= 0 and len(buf) - idx >= minimum:
                return bytes(buf[idx:])
        return None

    def _read_exact(self, count: int, timeout_s: float) -> bytes | None:
        """Read ``count`` more bytes, or None on timeout."""
        buf = bytearray()
        deadline = time.monotonic() + timeout_s
        while len(buf) < count and time.monotonic() < deadline:
            waiting = self.ser.in_waiting
            chunk = self.ser.read(min(waiting if waiting else 1, count - len(buf)))
            if chunk:
                buf.extend(chunk)
        if len(buf) != count:
            return None
        return bytes(buf)

    def _wait_for_dump_cli_ready(self, initial: bytes, *, timeout_s: float) -> bytes:
        """Consume the dump handler's trailing response before reusing the CLI."""
        response = bytearray(initial)
        deadline = time.monotonic() + timeout_s
        while b"Done" not in response and b"Error" not in response:
            if time.monotonic() >= deadline:
                break
            waiting = self.ser.in_waiting
            chunk = self.ser.read(waiting if waiting else 1)
            if chunk:
                response.extend(chunk)
        return bytes(response)

    def stats(self) -> str:
        """Firmware health line (frames/wraps/active/calib/rf_faults)."""
        return self.cmd("stats", 2.0)

    def stop_sensor(self) -> None:
        """Stop capture and verify the firmware returned to its idle CLI state."""
        self._require_done("sensorStop", self.cmd("sensorStop", 3.0))
        health = self.stats()
        self._require_done("stats", health)
        if "active=0" not in health:
            raise RuntimeError(f"IWR6843 remained active after sensorStop: {health.strip()}")

    def close(self) -> None:
        """Release the serial port."""
        self.ser.close()

    def __enter__(self) -> "IWR6843Radar":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
