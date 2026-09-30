#!/usr/bin/env python3
"""Prove the IWR6843 MSS <-> DSS detect link on a board (Phase 0).

The self-trigger's detect task is moving from the R4F (MSS) to the C674x
(DSS). Before any of it moves, this checks on the board that:

1. the DSS boots and answers over the mailbox (``trackCfg dsp ping``);
2. it reads the live L3 ring and scores a frame exactly as the MSS does,
   bit for bit (``trackCfg dsp probe``: the same l3_bin_score code on both);
3. how much faster it is, for the pre-impact scan plan's 27 bins and the
   whole window.

Usage (stop the kiosk first; it owns the port)::

    uv run python scripts/hardware-test/iwr6843_dsp_probe.py

Exit status 0 only when every ping answered and every probe matched.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "src")

from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402
from openflight.iwr6843.dsp_link import DspLinkError, summarize_probes  # noqa: E402

DEFAULT_CONFIG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
SCAN_PLAN_BINS = 27  # the pre-impact scan plan (l3_scan.h)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="an IQ16 .cfg profile")
    parser.add_argument("--port", default=None, help="CLI serial port (default: auto-detect)")
    parser.add_argument("--repeats", type=int, default=20, help="pings and probes per size")
    parser.add_argument("--settle-s", type=float, default=1.0, help="seconds after sensorStart")
    args = parser.parse_args()

    ok = True
    with IWR6843Radar(port=args.port) as radar:
        print(f"IWR6843 on {radar.port}")
        try:
            pings = [radar.dsp_ping() for _ in range(args.repeats)]
        except DspLinkError as exc:
            print(f"FAIL ping: {exc}")
            try:
                status = radar.dsp_status()
            except DspLinkError as status_exc:
                print(f"  no DSS status either ({status_exc}): flash the latest link image")
                return 1
            print(
                f"  DSS status: stage={status.stage}{' FAILED' if status.failed else ''} "
                f"err={status.err} beats={status.beats} served={status.served}"
                + (
                    f" exception pc={status.exc_pc:08x} efr={status.exc_efr:08x}"
                    if status.exc_pc is not None
                    else ""
                )
            )
            time.sleep(1.0)
            again = radar.dsp_status()
            print(f"  beats one second later: {again.beats} (rising means the DSS task runs)")
            try:
                hw = radar.dsp_hw()
            except DspLinkError as hw_exc:
                print(f"  no DSS hardware state ({hw_exc}): flash the latest link image")
                return 1
            print(
                f"  DSS hardware: stage in DSSGPREG0={hw.gpreg_stage} halted={hw.halted} "
                f"powered={hw.powered} (power={hw.power}) stc={hw.stc} "
                f"hs_ram_readback={'ok' if hw.hsram_ok else 'BAD'}"
            )
            print("  ESM status: " + " ".join(f"{word:08x}" for word in hw.esm))
            return 1
        print(f"ping: {len(pings)} answered, round trip {min(pings)}..{max(pings)} us")

        radar.send_config(args.config)
        time.sleep(args.settle_s)
        try:
            for bins in (SCAN_PLAN_BINS, None):
                probes = [radar.dsp_probe(bins) for _ in range(args.repeats)]
                summary = summarize_probes(probes)
                label = f"{probes[0].bins} bins" + (" (scan plan)" if bins else " (whole window)")
                prep = summary.dss_total_us_median - summary.dss_us_median
                print(
                    f"probe {label}: MSS {summary.mss_us_median:.0f} us, "
                    f"DSS {summary.dss_total_us_median:.0f} us "
                    f"(prepare {prep:.0f} + score {summary.dss_us_median:.0f}, "
                    f"gathered {summary.gathered}/{summary.count}) "
                    f"-> {summary.speedup:.1f}x faster, "
                    f"{summary.count - summary.mismatches}/{summary.count} matched"
                )
                for probe in probes:
                    if not probe.match:
                        print(
                            f"  MISMATCH slot={probe.slot} status={probe.status} "
                            f"mss_energy={probe.mss_energy} dss_energy={probe.dss_energy}"
                        )
                ok = ok and summary.mismatches == 0
        except DspLinkError as exc:
            print(f"FAIL probe: {exc}")
            ok = False
        finally:
            radar.stop_sensor()
    print("PASS: the DSS answers and scores the ring as the MSS does" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
