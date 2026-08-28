# PN532 NFC Club Tag Setup

OpenFlight can read an NFC tag stuck to the end of each club and switch the UI's
club selection automatically when that club is tapped against the reader. The
mapping from tag to club is *learned on the rig* and saved to disk, so a tag
carries no data of its own — a blank sticker straight off the roll works.

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
| Club tags | NTAG213 / NTAG215 or MIFARE Classic stickers, 25 mm round, one per club |
| Solderless cable kit | Female-Dupont jumpers for SPI (NSS, MOSI, MISO, SCK, IRQ, 3.3 V, GND) |
| Mounting | Thin double-sided mounting tape or nonconductive standoffs |

Any ISO14443A tag works: only the factory-programmed UID is read, never the
NDEF contents. Buy tags with an adhesive back sized to fit a grip cap or the
butt end of the shaft.

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
  corrected to match. The tag wins, because a club written onto the tag is what
  travels with the club between rigs.
- **The tag is unwritten but its UID is known** → the mapped club is selected.
  This is how MIFARE Classic cards and any tag learned before writing existed
  keep working.
- **The tag is blank and writable** → the kiosk offers to write a club onto it.
  See [Writing A Blank Tag](#writing-a-blank-tag).
- **The tag is unknown and cannot be written** → the kiosk asks which club it
  is and records the mapping against its UID, as before.

A tag holding somebody else's data — a URL, a business card — is never
offered for writing. Overwriting it is not a decision the tap flow makes on
your behalf; it takes the learn-by-UID path instead. To reuse such a tag,
erase it with a phone NFC app first, and it will then read as blank.

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

Match pins by silkscreen label. On V3 modules SPI is the 4-pin `NSS/MOSI/MISO/SCK`
header plus `IRQ` on the neighbouring header.

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

The learned tags are listed under **Club tags** in the kiosk menu sheet, each
with a **Forget** button. Forget a tag to re-teach it — useful when a tag was
taught the wrong club, or when a club is regripped and re-tagged. Tapping an
already-known tag while it is mapped simply selects its club; to move it to a
different club, forget it first and tap it again.

Two tags may point at the same club. That is deliberate: some builds tag both
the grip and the shaft.

## Writing A Blank Tag

A factory-fresh NTAG sticker has nothing on it. Tap one on the reader and the
kiosk runs the write flow:

1. **Blank tag** — the club grid, with the tag's UID shown and nothing
   preselected.
2. **Write 7 Iron to this tag?** — a confirmation naming the club and the tag.
   Nothing has been written yet; cancelling here leaves the tag untouched.
3. **Hold the club on the reader...** — the club is written onto the tag, read
   back to confirm, then mirrored into the rig's mapping and selected.

The write is verified by reading the tag back. A write that cannot be confirmed
is reported as a failure and changes nothing on the rig, so a half-written tag
never leaves OpenFlight believing a club it does not carry.

Keep the club still on the reader through step 3. If it moves, the flow reports
**Could not write the tag** and offers a retry with the club already chosen.

To re-point a written tag at a different club, erase it with a phone NFC app so
it reads as blank again, then tap it. Forgetting a tag in the menu clears the
rig's mapping but does not erase the tag, and the tag's own record wins on the
next tap.

## What Is Written On The Tag

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

Because it is ordinary NDEF, any phone with NFC Tools (or similar) can read and
write club tags:

- **Tag the whole bag from the sofa** — write each club id as a text record and
  the rig will read them without ever running the write flow.
- **Diagnose a tag** without the rig, by reading what it actually holds.
- **Erase a tag** to make it blank again.

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
   kiosk to write the tag (blank) or learn it (unwritable).
5. Shows a large **Club selected — 7 Iron** confirmation on the kiosk for about
   two seconds, and closes the club picker if it was open. The confirmation is
   not tappable and cannot swallow a tap while it fades.
6. Records the tap in the session log.

In mock mode (`--mock --nfc`) a mock reader replaces the hardware, so the whole
learn-and-select flow can be exercised on a laptop with no PN532 attached.

## Session Logging

The `session_start` entry records:

- Whether the reader initialized.
- Reader type, host interface (SPI by default), SPI bus/CE, and IRQ GPIO.
- Tag file path and how many tags were loaded.
- Initialization error, when there was one.

Each tap writes an `nfc_scan` entry with the UID, the resolved club, where that
club came from (`tag` or `registry`), and whether the tag was blank or writable.
Learning, writing, or forgetting a tag writes a `club_tag_change` entry with the
action taken.

## Troubleshooting

### `/dev/spidev0.0` Is Missing

Add `dtparam=spi=on` to `/boot/firmware/config.txt` (keep `dtparam=i2c_arm=on`
for the inclinometer) and reboot. Then `ls -l /dev/spidev0.0`.

### `PN532 did not acknowledge the command`

The SPI write happened, but IRQ never went active. Almost always mode
selection: the board is still in UART or I2C. Match the **SPI** row printed
on the board, then power-cycle — the chip latches the interface at power-up.
On a clone whose table is ambiguous, try all four switch pairs, power-cycling
between them.

Also confirm `IRQ` is on physical pin 15 (GPIO22), not `RSTO`. `RSTO` is an
output; leave it unconnected.

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
so forgetting it in the menu will not help. Erase the tag with a phone NFC app
and tap it again to run the write flow, or write the right club id straight
onto it from the phone.

If the tag carries nothing, it was learned as that club: open the kiosk menu,
find it under **Club tags**, press **Forget**, then tap it again.

### A Blank Tag Asks To Be Learned Instead Of Written

The reader could not read the tag's memory, so it does not know the tag is
blank. Either it is a MIFARE Classic tag, which this driver reads by UID only,
or it is a Type 2 tag that has never been NDEF-formatted. Formatting it with a
phone NFC app makes the write flow available.

### "Could not write the tag"

The club moved off the reader mid-write, or the tag is write-protected. Hold the
club steady against the antenna and press **Try again**. Nothing was recorded on
the rig, so a retry is safe. If it fails repeatedly, confirm the tag is not
locked — some tags ship read-only, and NTAG lock bits are irreversible once set.

### The Learn Prompt Reappears For A Tag Already Learned

The mapping did not persist. Check that `~/.openflight` is writable by the
OpenFlight user and that the disk is not full; a failed write is reported in the
UI and logged. Check for a `club_tags.json.corrupt` file, which means the
previous file could not be parsed.

### Nothing Happens When A Tag Is Tapped

- Confirm the startup banner printed `NFC club tags enabled`.
- Confirm `--nfc` was actually passed; without it the reader is never opened.
- Run `read_pn532.py` to separate a reader problem from a kiosk problem.
- Check the log for repeated `[NFC] Read failed` lines, which point at wiring.

## Current Limitations

- Only NFC Forum Type 2 tags (NTAG213/215/216, MIFARE Ultralight) can be
  written. MIFARE Classic cards — including the card and keyfob in most PN532
  kits — are read-only here, and work through the learn-by-UID flow. Writing
  them would need key authentication and block writes.
- A tag holding non-club data is never overwritten by the rig; erase it with a
  phone first.
- Only ISO14443A tags are read at all. ISO15693 (NFC Type 5, including ST25DV)
  tags are not, since the PN532's Type 5 support is not implemented here.
- SPI bus, CE, IRQ GPIO, and tag file are command-line settings rather than a
  persisted rig configuration file.
- The reader selects clubs only; it does not switch players or start sessions.
