#!/usr/bin/env python3
"""Print the IWR6843 ball-leave detector as it runs.

Stop the kiosk first. This owns the TI UART, arms triggerCfg, turns on
debugCfg, and prints one line per frame until you press Ctrl+C.
"""

from __future__ import annotations

import argparse
import time

from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M
from openflight.iwr6843.driver import TRIGGER_NOTICE, IWR6843Radar
from openflight.iwr6843.monitor import measure_trigger_level, tee_local_bin

_DEFAULT_CFG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None)
    parser.add_argument("--config", default=_DEFAULT_CFG)
    parser.add_argument("--tee-m", type=float, default=DEFAULT_TEE_RANGE_M)
    parser.add_argument(
        "--level",
        type=float,
        default=None,
        help="tee power threshold; omitted means 1.5x the measured tee floor",
    )
    parser.add_argument("--hits", type=int, default=2)
    args = parser.parse_args()

    local_bin = tee_local_bin(args.tee_m, args.config)
    radar = IWR6843Radar(port=args.port)
    try:
        radar.send_config(args.config)
        # A fixed level of 1000 is below a person sitting in the beam (the tee
        # return is ~2e5), so the detector arms on them and fires. Sample the
        # live tee first unless the caller named a threshold.
        if args.level is None:
            floor, level = measure_trigger_level(radar, local_bin, args.hits)
            print(f"tee p95 {floor:.0f}; arming at {level:.0f}", flush=True)
        else:
            level = args.level
        # Debug first so the frames right after arming are visible: a fire in
        # that window used to be swallowed by the next command's buffer reset.
        reply = radar.cmd("debugCfg 1")
        if "Done" not in reply:
            raise SystemExit(f"debugCfg rejected: {reply.strip()}")
        print(
            f"watching local bin {local_bin}, level {level:g}, {args.hits} hits. Ctrl+C to stop.",
            flush=True,
        )
        reply = radar.cmd(f"triggerCfg {local_bin} {level} {args.hits}")
        if "Done" not in reply:
            raise SystemExit(f"triggerCfg rejected: {reply.strip()}")
        print(reply.replace("Done", "").strip(), flush=True)
        # The arming reply itself may carry the notice.
        pending = reply.encode()
        while True:
            if TRIGGER_NOTICE in pending:
                pending = b""
                # Debug text shares this UART with the binary release. Silence
                # it for the exchange, then turn it back on to keep watching.
                radar.cmd("debugCfg 0", window=1.0)
                radar.release_sparse_freeze()
                reply = radar.cmd("debugCfg 1")
                if "Done" not in reply:
                    raise SystemExit(f"debugCfg rejected: {reply.strip()}")
                print("\n-- fired: released the frozen ring, watching again --", flush=True)
            else:
                pending = pending[-(len(TRIGGER_NOTICE) - 1) :]
            waiting = radar.ser.in_waiting
            chunk = radar.ser.read(waiting or 1)
            if not chunk:
                time.sleep(0.02)
                continue
            print(chunk.decode(errors="replace"), end="", flush=True)
            pending += chunk
    except KeyboardInterrupt:
        print()
    finally:
        try:
            radar.cmd("debugCfg 0", window=0.5)
        except Exception:
            pass
        radar.close()


if __name__ == "__main__":
    main()
