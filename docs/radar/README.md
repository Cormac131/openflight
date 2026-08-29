# OPS243 vendor documentation

Vendor PDFs for the OPS243-A Doppler radar, kept here so a build doesn't depend
on OmniPreSense's site staying up. Check the source URL before assuming a copy
is current — OmniPreSense revises these in place under new revision letters.

| File | Rev | Date | Source |
|---|---|---|---|
| [`AN-010-AD_API_Interface.pdf`](AN-010-AD_API_Interface.pdf) | AD | Oct 2025 | [omnipresense.com](https://omnipresense.com/wp-content/uploads/2025/10/AN-010-AD_API_Interface.pdf) |
| [`AN-027-A_Rolling Buffer.pdf`](<AN-027-A_Rolling Buffer.pdf>) | A | Jun 2025 | [omnipresense.com](https://omnipresense.com/wp-content/uploads/2025/06/AN-027-A_Rolling-Buffer-1.pdf) |
| [`AN-029-A_OPS243-for-Sports.pdf`](AN-029-A_OPS243-for-Sports.pdf) | A | Jun 2026 | [omnipresense.com](https://omnipresense.com/wp-content/uploads/2026/06/AN-029-A_OPS243-for-Sports_260621.pdf) |
| [`OmniPreSense_Sports_Ball_Detect_2507.pdf`](OmniPreSense_Sports_Ball_Detect_2507.pdf) | — | Jul 2025 | Vendor slide deck, not published on the site |
| [`OPS-DS-003-01_OPS243-Datasheet.pdf`](OPS-DS-003-01_OPS243-Datasheet.pdf) | 0.1 | — | Not publicly linked; request from OmniPreSense customer service |

## Which sports note to read

**AN-029 rev A** supersedes the July 2025 `Sports_Ball_Detect` slide deck for
everything except three items, so the deck is kept rather than deleted:

- Saving the tuned configuration to persistent memory with `A!` — AN-029 never
  mentions it, and `scripts/hardware-test/test_rolling_buffer_persist.py --setup`
  depends on that step.
- The 10° up-angle sensor recommendation for golf, dropped rather than
  contradicted in AN-029.
- The `R+` inbound-only direction filter, illustrated on the water polo slide.

Two errors in AN-029 rev A worth knowing before copying settings out of it:

- Page 7 says to set FFT size with `S>32`. That is wrong — the correct command
  is `X=32`, as its own Table 6 states.
- It gives the streaming report rate for 30 ksps / 128 samples / 4096 FFT as
  "near 200 Hz (5 ms)", where the 2025 deck gives 56 Hz (18 ms) for identical
  settings. ~200 Hz is the theoretical frame rate (128 / 30000 = 4.27 ms); 56 Hz
  appears to include processing overhead. Rolling buffer mode doesn't depend on
  the streaming rate either way.

Table 6 of AN-029 otherwise matches the golf configuration in `src/openflight/ops243.py`,
and adds a `R>10` speed filter that OpenFlight does not set. AN-029 page 8 cites
OpenFlight as reference code for an OPS243-A launch monitor.
