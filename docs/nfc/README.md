# NFC Club Tag Setup

OpenFlight can read an NFC tag stuck to the end of each club and switch the UI's
club selection automatically when that club is tapped against the reader.

Two reader chips are supported, selected with `--nfc-reader`:

| `--nfc-reader` | Chip | Tags it can read | Host link |
|----------------|------|-------------------|-----------|
| `pn532` (default) | NXP PN532 | ISO14443A only (NTAG213/215, MIFARE Classic) | SPI, or I2C as a fallback |
| `pn5180` | NXP PN5180 | ISO14443A **and** ISO15693 (adds ICODE SLIX/SLIX2 — including Shot Scope's watch tags) | SPI only |

Most of this document covers the PN532, which is the simpler and cheaper build.
See [PN5180 Setup](#pn5180-setup) below if you specifically want to read
ISO15693 tags — the PN532 cannot see those at all, regardless of software.

A writable tag (NTAG213/215) can carry the club as an NDEF text record, so the
club travels with the sticker between rigs. A blank sticker still works: the
mapping from its factory UID to a club is learned on the rig and saved to disk.

This is an optional feature. It is disabled unless `--nfc` is passed.

If the reader is missing, unplugged, or temporarily unreadable, OpenFlight keeps
running normally and club selection stays manual.

> [!IMPORTANT]
> The **ST25DV16K is a tag, not a reader.** It is a dynamic NFC tag with an I2C
> side-channel for the host it is soldered to; it has no initiator/reader mode
> and cannot read another tag. A reader IC is required to read tags on clubs,
> which is why this integration targets the PN532.

## What To Buy

The hardware this integration targets is:

| Part | Product |
|------|---------|
| NFC reader breakout | [Adafruit PN532 NFC/RFID Controller Breakout, product 364](https://www.adafruit.com/product/364), or any generic **PN532 V3 module** (the red Elechouse-style board sold as "PN532 NFC RFID V3 Kit") |
| Club tags | NTAG213 / NTAG215 or MIFARE Classic stickers, 25 mm round, one per club. Not Shot Scope ICODE SLIX tags (see below). |
| Solderless cable kit | Female-Dupont jumpers for SPI (NSS, MOSI, MISO, SCK, IRQ, 3.3 V, GND) |
| Mounting | Thin double-sided mounting tape or nonconductive standoffs |

Any ISO14443A tag works. The reader takes the factory UID and, on NFC Forum
Type 2 tags, the NDEF contents. Buy tags with an adhesive back sized to fit a
grip cap or the butt end of the shaft.

The PN532 is the reader this section is about. Firmware open and ISO14443A
UIDs are working on SPI; keep using that. Stick an NTAG on the grip even if
the club already has a Shot Scope tag. Shot Scope black RFID tags are NXP
ICODE SLIX (ISO15693 / NFC Type 5). The PN532 cannot inventory those, so a
tap of a Shot Scope tag will do nothing on this reader. Leave the Shot Scope
tag for the watch; OpenFlight uses the NTAG -- or build with a PN5180
instead, which can read the Shot Scope tag directly (see
[PN5180 Setup](#pn5180-setup)).

Do not buy a bare PN532 chip. Use a breakout with the regulator and antenna
already installed.

### Generic PN532 V3 Modules

The cheap red "PN532 V3" boards work: same NXP silicon, same command set, and
no software difference. Their larger PCB antenna usually reads a little
further than the Adafruit board's, which suits a club tapped against the
enclosure. Three things differ in practice:

- The mode switches are usually silkscreened **SET0/SET1** rather than
  SEL0/SEL1. See the next section — read the table printed on your board.
- Their I2C pull-up resistors (unused in SPI mode) tie to the module's own
  `VCC`. **Still power from 3.3 V.** See the warning under [Wiring](#wiring).
- Pin order on the headers varies between clones. Match by label, never by
  position in a photo.

These kits normally include a MIFARE Classic card and keyfob. Both are
ISO14443A, so they read fine and are useful for bring-up before the club
stickers arrive.

## How Club Selection Works

Four things stay separate:

```text
tag UID          = factory-programmed, unique, unchangeable
tag contents     = the club written onto the tag, as an NDEF text record
club mapping     = learned on the rig, stored in ~/.openflight/club_tags.json
active club      = what the UI and shot pipeline currently use
```

Tapping a tag reads both its UID and its contents, then:

- **The tag names a club** → that club is selected, and the rig's mapping is
  corrected to match. The tag wins, because a club written onto the tag (for
  example from a phone) is what travels with the club between rigs.
- **The tag has no club record but its UID is known** → the mapped club is
  selected.
- **The tag is unknown** → the kiosk asks which club it is and records the
  mapping against its UID. Blank tags, read-only cards, and tags with
  unrecognized contents all take this path. The kiosk does not write onto the
  tag.

A tag holding somebody else's data — a URL, a business card — is treated as
unknown unless that UID is already learned. The NDEF text is ignored when it
is not a club id OpenFlight recognizes.

In every case the selection is the same as if the club had been tapped on the
picker, and the picker closes if it was open.

## Power Down Before Wiring

Shut down the Pi and remove power before connecting or moving GPIO wires. A
misplaced 3.3 V lead can short the Pi, cause reboot loops or a black screen, and
potentially damage the Pi or the reader.

## Set The PN532 To SPI Mode

PN532 boards ship in UART/HSU mode. Move the two onboard DIP switches to select
**SPI** before wiring. On both the Adafruit breakout and Elechouse-style V3
modules that is typically the opposite of I2C:

| Switch | Position for SPI |
|--------|------------------|
| SEL0 / SET0 | OFF (0) |
| SEL1 / SET1 | ON (1) |

> [!IMPORTANT]
> Clone boards do not all label or orient their switches the same way. Every
> board prints its own mode table beside the switches — **that table wins over
> this one.** Look for the row marked SPI and match it.

The chip latches the interface at power-up: change the switches, then
power-cycle. Do not leave it in I2C. I2C shares SDA/SCL with the LIS3DH and
the Geekworm fuel gauge; a PN532 that stretches SCL takes those devices down with
it.

## Wiring

SPI uses its own pins. Leave physical pins 3 and 5 for the inclinometer.

```text
PN532 breakout                           Raspberry Pi GPIO header

NSS / SS    ---------------------------->  physical pin 24 (GPIO8 / SPI0 CE0)
MOSI        ---------------------------->  physical pin 19 (GPIO10 / MOSI)
MISO        ---------------------------->  physical pin 21 (GPIO9 / MISO)
SCK         ---------------------------->  physical pin 23 (GPIO11 / SCLK)
IRQ         ---------------------------->  physical pin 15 (GPIO22)
RSTO        --- leave unconnected ---
VCC / 3.3V  ---------------------------->  physical pin 17 (3.3V)
GND         ---------------------------->  physical pin 20 (GND)
```

| PN532 | Raspberry Pi physical pin | Pi signal |
|-------|---------------------------|-----------|
| `NSS` / `SS` / `SSEL` | **24** | GPIO8 / SPI0 CE0 |
| `MOSI` | **19** | GPIO10 |
| `MISO` | **21** | GPIO9 |
| `SCK` | **23** | GPIO11 |
| `IRQ` | **15** | GPIO22 |
| `RSTO` | — | leave unconnected (chip output, not a reset input) |
| `VCC` / `3.3V` | **17** | 3.3 V power |
| `GND` | **20** | Ground |

> [!WARNING]
> **RSTO is not reset.** It is `RSTOUT_N`, an output that goes low while the chip
> is in reset. Do not drive it from a Pi GPIO. A reset *into* the chip is
> `RSTPD_N`, which Elechouse V3 boards usually do not break out.

> [!WARNING]
> Power the reader from **3.3 V**, not 5 V. Elechouse SPI is 3.3 V TTL. Stay
> off GPIO6 (Geekworm charger), GPIO16 (HAT), GPIO17 (sound trigger), and
> GPIO14/15 (OPS UART).

NSS / SS is **required** on physical pin 24 (kernel SPI0 CE0). The driver
cannot claim GPIO8 as a gpiozero pin — SPI already owns it (`GPIO busy`).
IRQ is optional.

## Mounting And Read Range

Mount the reader where a club can be presented without a swing hitting it — the
side of the enclosure or a small bracket beside the hitting mat both work. Read
range for a 25 mm sticker is roughly 20–30 mm, so the tap must be deliberate.

Two things kill read range:

- **Metal behind the tag.** A tag stuck directly to a steel shaft or a metal
  grip cap detunes the antenna. Use ferrite-backed ("on-metal") tags there, or
  mount the tag on the rubber grip instead.

- **Metal behind the reader.** Keep the PN532 antenna at least 10 mm clear of
  the Pi, the radar shielding, and any enclosure metal.

Tag the butt end of the grip, not the head. The head is the part that swings.

## Enable SPI On The Pi

Keep I2C enabled for the inclinometer and Geekworm gauge. Add SPI and reboot:

```bash
# Raspberry Pi OS Bookworm: /boot/firmware/config.txt
dtparam=i2c_arm=on
dtparam=spi=on
```

```bash
sudo reboot
```

After reconnecting over SSH:

```bash
ls -l /dev/spidev0.0
```

`i2cdetect` will **not** show the PN532. That is expected. The inclinometer
should still appear at `18` and the fuel gauge at `36 UU`.

## Install Software

From the OpenFlight checkout:

```bash
uv sync
```

The Linux installation includes `spidev` and `gpiozero`, which the PN532 driver
uses for `/dev/spidev0.0` and the IRQ pin. Add the OpenFlight user to the `spi`
and `gpio` groups if `Permission denied` appears:

```bash
sudo usermod -aG spi,gpio "$USER"
sudo reboot
```

## Verify Raw Reads

Run the standalone hardware readout before starting the full application:

```bash
uv run python scripts/hardware-test/read_pn532.py
```

Example output:

```text
PN532 detected on SPI-0.0 IRQ GPIO22 (firmware 1.6)
Club tags: 3 learned in /home/pi/.openflight/club_tags.json
Present a tag to the antenna (Ctrl-C to stop)...
04:A2:B1:C3  7-iron
04:5F:1E:88  (not learned)
```

Useful options:

```bash
# Read 5 tags and exit
uv run python scripts/hardware-test/read_pn532.py --count 5

# Teach the next tag presented, without starting the kiosk
uv run python scripts/hardware-test/read_pn532.py --assign 7-iron

# Probe a non-default SPI device or IRQ pin
uv run python scripts/hardware-test/read_pn532.py --spi-device 1 --irq-gpio 23
```

## Learn The Club Tags

Tags are learned from the kiosk, one tap each. There is no separate pairing
mode to remember:

1. Start OpenFlight with `--nfc`.
2. Tap an unlearned club against the reader.
3. The kiosk shows **New club tag** with the tag's UID and the club grid.
4. Pick the club. The mapping is written to disk immediately and that club
   becomes active.
5. Repeat for the rest of the bag.

Dismissing the prompt with the ✕ leaves the tag unlearned; the next tap asks
again.

Tapping a known tag selects its club and opens the club-tag dialog for that
tag only. **Forget** lives there, not in the menu — useful when a tag was
taught the wrong club, or when a club is regripped and re-tagged. After
Forget, pick the club again on the same dialog, or dismiss and tap later.
If `--nfc` is set but the PN532 fails to start, the menu shows a reader
error and no tag list.

Two tags may point at the same club. That is deliberate: some builds tag both
the grip and the shaft.

## What May Already Be On The Tag

The club id goes onto the tag as a standard **NDEF text record** — the same
thing a phone NFC app writes:

```text
NDEF text record, UTF-8, language "en"
payload: "7-iron"
```

The text is the club id exactly as OpenFlight names it: `driver`, `3-wood`,
`5-hybrid`, `7-iron`, `pw`, `gw`, `sw`, `lw`, and so on. `uv run python -c
"from openflight.launch_monitor import ClubType; print([c.value for c in
ClubType])"` prints the full list.

Because it is ordinary NDEF, any phone with NFC Tools (or similar) can read
and write club tags. The kiosk only reads tags; it never writes a club onto
one. Use a phone to:

- **Tag the whole bag from the sofa** — write each club id as a text record and
  the rig will read them without learning by UID first.
- **Diagnose a tag** without the rig, by reading what it actually holds.
- **Erase a tag** so the next tap uses the learned UID mapping, or asks again.

A tag whose text is not a club OpenFlight recognizes is ignored, and the rig
falls back to whatever it learned for that UID.

## Where The Mapping Lives

Learned tags are stored as JSON at `~/.openflight/club_tags.json`:

```json
{
  "version": 1,
  "tags": {
    "04A2B1C3": {
      "uid": "04A2B1C3",
      "uid_display": "04:A2:B1:C3",
      "club": "7-iron",
      "learned_at": "2026-08-27T09:14:02.113000+00:00",
      "last_seen_at": "2026-08-27T10:02:44.507000+00:00"
    }
  }
}
```

Notes on the file:

- Every change rewrites it atomically (write to a temp file, then rename), so
  pulling the power mid-write cannot truncate it.
- Back it up to keep a bag's tags across an SD card rebuild, or copy it to a
  second rig to share one set of tags.
- `--nfc-tags-file` points at a different path, e.g. one file per bag.
- A file that cannot be parsed is renamed to `club_tags.json.corrupt` and the
  rig starts with an empty registry rather than refusing to boot. Individual
  rows naming a club OpenFlight does not know are dropped; the rest are kept.

## Start OpenFlight

Example production startup:

```bash
scripts/start-kiosk.sh --nfc
```

With a non-default SPI device, IRQ pin, or tag file:

```bash
scripts/start-kiosk.sh \
  --nfc \
  --nfc-spi-bus 0 \
  --nfc-spi-device 0 \
  --nfc-irq-gpio 22 \
  --nfc-tags-file /home/pi/bags/sunday.json
```

At startup, OpenFlight prints the reader firmware, the number of tags learned,
and the tag file path. It then polls the reader about six times a second.

For each tap, OpenFlight:

1. Reads the tag UID and normalizes it (case and separators are irrelevant),
   and reads the tag's contents if it is an NFC Forum Type 2 tag.
2. Suppresses repeats of the same UID for three seconds, so a tag resting on the
   antenna is one club change rather than a stream of them.
3. Takes the club from the tag's own record, else from the learned mapping.
4. Selects that club and broadcasts it to every connected client, or asks the
   kiosk to learn an unknown tag against its UID.
5. Shows a large **Club selected — 7 Iron** confirmation on the kiosk for about
   two seconds, and closes the club picker if it was open. The confirmation is
   not tappable and cannot swallow a tap while it fades.
6. Records the tap in the session log.

`--mock` only replaces the radar. `--nfc` always talks to the reader; omit the
flag when no reader is attached.

## PN5180 Setup

The PN5180 is a newer NXP reader chip. Build with it instead of the PN532
if you specifically want to read ISO15693 tags directly -- most notably Shot
Scope's black watch tags (NXP ICODE SLIX), which the PN532 cannot see at all.
Everything else in this document (club selection logic, the tag file, NDEF
text records, session logging) works identically; only the reader chip and
its wiring differ.

> [!NOTE]
> This driver has not been validated against physical PN5180 silicon in this
> repository. The command framing and register map follow the NXP PN5180
> datasheet; if bring-up behaves differently than described here, that is
> more likely to be a wrong assumption in the driver than in this doc --
> please file an issue with what you saw.

### What To Buy

| Part | Product |
|------|---------|
| NFC reader breakout | A PN5180 breakout board (e.g. the "PN5180 NFC Module" sold by several sellers on AliExpress/Amazon) exposing MOSI, MISO, SCK, NSS, BUSY, RST, 3.3V, GND |
| Tags | NTAG213/215 for ISO14443A (same as the PN532 section above), or ICODE SLIX/SLIX2 stickers for ISO15693 |

The PN5180 is 3.3V logic. Do not power it from 5V.

### Wiring

Unlike the PN532, the PN5180 has no IRQ pin to wire; it has BUSY (an output,
required) and RST/NRESET (an input, recommended but some boards leave it
tied high internally if unconnected).

```text
PN5180 breakout                          Raspberry Pi GPIO header

NSS         ---------------------------->  physical pin 24 (GPIO8 / SPI0 CE0)
MOSI        ---------------------------->  physical pin 19 (GPIO10 / MOSI)
MISO        ---------------------------->  physical pin 21 (GPIO9 / MISO)
SCK         ---------------------------->  physical pin 23 (GPIO11 / SCLK)
BUSY        ---------------------------->  physical pin 16 (GPIO23)
RST         ---------------------------->  physical pin 18 (GPIO24)
VCC / 3.3V  ---------------------------->  physical pin 17 (3.3V)
GND         ---------------------------->  physical pin 20 (GND)
```

| PN5180 | Raspberry Pi physical pin | Pi signal |
|--------|---------------------------|-----------|
| `NSS` | **24** | GPIO8 / SPI0 CE0 |
| `MOSI` | **19** | GPIO10 |
| `MISO` | **21** | GPIO9 |
| `SCK` | **23** | GPIO11 |
| `BUSY` | **16** | GPIO23 (default; override with `--nfc-busy-gpio`) |
| `RST` | **18** | GPIO24 (default; override with `--nfc-reset-gpio`) |
| `VCC` / `3.3V` | **17** | 3.3 V power |
| `GND` | **20** | Ground |

The same warnings from the PN532 [Wiring](#wiring) section apply: power from
3.3 V, and stay off GPIO6/16/17/14/15, which other OpenFlight peripherals use.

Enable SPI the same way as for the PN532 -- see
[Enable SPI On The Pi](#enable-spi-on-the-pi) -- and add the OpenFlight user
to the `spi` and `gpio` groups if needed.

### Verify Raw Reads

```bash
uv run python scripts/hardware-test/read_pn5180.py
```

Example output:

```text
PN5180 detected on SPI-0.0 BUSY GPIO23 RESET GPIO24 (firmware 4.0, product 3.0)
Club tags: 3 learned in /home/pi/.openflight/club_tags.json
Present a tag to the antenna -- ISO14443A or ISO15693 (Ctrl-C to stop)...
04:A2:B1:C3  7-iron
E0:04:01:50:12:34:56:78  (not learned)
```

The second line above is an ISO15693 UID (8 bytes, starting with NXP's `E0`
manufacturer code) -- a Shot Scope tag would read exactly like this. Useful
options mirror `read_pn532.py`: `--count`, `--assign CLUB`, `--tags-file`,
plus `--spi-bus`, `--spi-device`, `--busy-gpio`, and `--reset-gpio` for
non-default wiring.

### Start OpenFlight With The PN5180

```bash
scripts/start-kiosk.sh --nfc --nfc-reader pn5180
```

With non-default SPI or GPIO settings:

```bash
scripts/start-kiosk.sh \
  --nfc \
  --nfc-reader pn5180 \
  --nfc-spi-bus 0 \
  --nfc-spi-device 0 \
  --nfc-busy-gpio 23 \
  --nfc-reset-gpio 24 \
  --nfc-tags-file /home/pi/bags/sunday.json
```

`--nfc-interface`, `--nfc-irq-gpio`, `--nfc-i2c-bus`, and `--nfc-i2c-address`
are PN532-only and are ignored for `--nfc-reader pn5180`.

A tap of an ISO15693 tag flows through the exact same pipeline as an
ISO14443A one: [How Club Selection Works](#how-club-selection-works) applies
unchanged, including NDEF text records on ISO15693 tags formatted as an NFC
Forum Type 5 tag. A raw factory tag with no NDEF content -- which is what a
Shot Scope watch tag is -- falls back to the learn-by-UID flow just like a
blank NTAG or a MIFARE Classic card does on the PN532.

## Session Logging

The `session_start` entry records:

- Whether the reader initialized.
- Reader type, host interface (SPI by default), and its bus/CE/GPIO settings.
- Tag file path and how many tags were loaded.
- Initialization error, when there was one.

Each tap writes an `nfc_scan` entry with the UID, the resolved club, where that
club came from (`tag` or `registry`), and whether the tag was blank or writable.
Learning or forgetting a tag writes a `club_tag_change` entry with the
action taken.

## Troubleshooting

### `/dev/spidev0.0` Is Missing

Add `dtparam=spi=on` to `/boot/firmware/config.txt` (keep `dtparam=i2c_arm=on`
for the inclinometer) and reboot. Then `ls -l /dev/spidev0.0`.

### `PN532 did not acknowledge the command`

The SPI write happened, but the chip never reported ready. Match the **SPI**
row printed on the board, then power-cycle. On a clone whose table is
ambiguous, try all four switch pairs, power-cycling between them.

Also confirm `NSS` is on physical pin 24 (CE0), MOSI/MISO/SCK are not
swapped, and `IRQ` is on physical pin 15 (GPIO22) not `RSTO`. The driver
polls SPI status as well as IRQ, so a missing IRQ wire should not block ACK
if the SPI header is correct.

### Permission Denied On `/dev/spidev0.0`

```bash
sudo usermod -aG spi,gpio "$USER"
sudo reboot
```

`gpio` is required for the IRQ pin as well as `spi` for `/dev/spidev0.0`.

### `i2cdetect` Still Shows `24`

The PN532 is still on I2C. That is the old wiring. Power down, move the DIP
switches to SPI, wire NSS/MOSI/MISO/SCK/IRQ as in [Wiring](#wiring), and
unplug SDA/SCL from pins 3 and 5. After a power-cycle, `i2cdetect -y 1`
should show `18` and `36 UU` only — never `24`.

Do not set `dtparam=i2c_arm=off`. That takes down the LIS3DH while the
Geekworm address can still print `UU`.

### Tags Read Intermittently Or Only At One Angle

- Confirm `VCC` is a Pi 3.3 V header pin (physical pin 17), not a downstream
  STEMMA QT port on the LIS3DH. RF bursts of about 100 mA through that
  board's regulator can brown out the inclinometer.
- Move the reader further from metal, especially the radar shielding.
- Switch to on-metal (ferrite-backed) tags if the tag sits on a metal cap.
- Tap flat against the antenna coil, not edge-on.
- Confirm with `read_pn532.py`, which prints every tag it sees.

### A Tag Selects The Wrong Club

If the club is written on the tag, the tag wins over anything the rig learned,
so forgetting it on the kiosk will not help. Erase the tag with a phone NFC app
and tap it again to learn it, or write the right club id onto it from the
phone.

If the tag carries nothing, it was learned as that club: tap it, press
**Forget** on the club-tag dialog, then pick the club again.

### The Learn Prompt Reappears For A Tag Already Learned

The mapping did not persist. Check that `~/.openflight` is writable by the
OpenFlight user and that the disk is not full. A failed write stays on the
learn prompt so you can retry, and is logged. Check for a
`club_tags.json.corrupt` file, which means the previous file could not be
parsed.

### Nothing Happens When A Tag Is Tapped

- Confirm the startup banner printed `NFC club tags enabled`. If the menu
  shows **Club tags** with **Reader — Not connected** and no tag list,
  `--nfc` was passed but the PN532 did not start; the error text under that
  row is the init failure.
- Confirm `--nfc` was actually passed; without it the reader is never opened.
- `--mock` only replaces the radar. `--nfc` still needs the PN532.
- Run `read_pn532.py` to separate a reader problem from a kiosk problem.
- Check the log for repeated `[NFC] Read failed` lines, which point at wiring.
- A Shot Scope ICODE SLIX tag will never appear. Confirm with the kit card or
  an NTAG sticker first.

## Current Limitations

- The kiosk never writes tags. A phone NFC app can write an NDEF club record
  onto NFC Forum Type 2 tags (NTAG213/215/216, MIFARE Ultralight) or, on a
  PN5180 build, an NFC Forum Type 5 (ISO15693) tag. MIFARE Classic cards —
  including the card and keyfob in most PN532 kits — still work through the
  learn-by-UID flow, as do raw (non-NDEF) ISO15693 tags such as Shot Scope's.
- The PN532 only reads ISO14443A tags. ISO15693 / NFC Type 5 — including NXP
  ICODE SLIX, SLIX2, ST25DV, and Shot Scope watch RFID tags — is a hardware
  limit of that chip, not a missing poll in this driver; use `--nfc-reader
  pn5180` (see [PN5180 Setup](#pn5180-setup)) to read those tags instead.
- The PN5180 driver has not been run against physical silicon in this
  repository; see the note at the top of [PN5180 Setup](#pn5180-setup).
- SPI bus, CE, IRQ/BUSY/RESET GPIO, and tag file are command-line settings
  rather than a persisted rig configuration file.
- The reader selects clubs only; it does not switch players or start sessions.
