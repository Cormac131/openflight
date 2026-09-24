# Wi-Fi, Bluetooth and Desktop Access

The kiosk runs fullscreen, so the desktop's network icons are hidden. The
**Connections** panel lets you manage Wi-Fi and Bluetooth from the touchscreen
without a keyboard and without closing OpenFlight.

## Where to find it

- **Header:** the Wi-Fi and Bluetooth icons at the top right of every screen
  open the panel on the matching tab. The Wi-Fi icon shows signal bars, and a
  `!` badge means the Pi is on a network but can't reach the internet.
- **Club picker:** the same icons appear on the club picker that opens at
  launch, so you can set up Wi-Fi before your first shot.
- **Menu:** *Menu → System → Wi-Fi & Bluetooth*.

These controls only appear on the Pi's own screen. Phones, tablets and TVs
viewing OpenFlight over the network never see them, and the server refuses
the underlying requests from those devices (see [Security model](#security-model)).

## Wi-Fi

- **Current network:** the name, signal strength and IP address, with
  **Disconnect**, **Forget** and **Turn off/on** buttons.
- **Scan** lists nearby networks. Tap a network to join it:
    - Open networks and networks you have saved connect immediately.
    - For a secured network you haven't saved, an on-screen keyboard opens.
      Tap **Show** to check what you typed. WPA passwords are 8–63 characters,
      or a 64-digit hex key.
    - If the password is wrong, OpenFlight says so and doesn't save the network.
    - **Other network…** joins a hidden network by name.
- Enterprise (802.1X) and WEP networks appear in the list, but you can't join
  them from the kiosk.
- **Forget** removes a saved network, including its password.

### Local network vs internet

The status line at the top of the panel reports these separately:

| Local network | Internet | Meaning |
|---|---|---|
| Connected | Available | Normal. Cloud sync and updates work. |
| Connected | Sign-in required | Captive portal (hotel or club guest Wi-Fi). |
| Connected | Local only | Simulators and phones on the same network work; there's no internet. |
| Not connected | Not available | Standalone. OpenFlight still measures shots. |

OpenFlight doesn't need the internet. Shot measurement, the UI, session logs
and local simulator connections all keep working offline. Internet
reachability comes from NetworkManager's connectivity check when it's enabled.
Otherwise OpenFlight briefly tests a TCP connection to a few public anycast
addresses (`1.1.1.1`, `8.8.8.8`, `9.9.9.9`) at most every 30 seconds.

## Bluetooth

- **Turn on/off** powers the adapter. If Bluetooth is rfkill-blocked, OpenFlight
  unblocks it (this needs the setup step below).
- **Find devices** searches for about 30 seconds. Put the device in pairing mode
  first.
- **Pair**, **Connect**, **Disconnect** and **Forget** work on each device.
- If a device needs a code, a prompt appears on top of whatever is on screen:
    - **Confirm:** check that both screens show the same six digits, then tap **Pair**.
    - **Enter code:** type the six digits shown on the device on the keypad.
    - **PIN:** type the PIN (often `0000` or `1234`).
    - **Display:** type the code shown on the kiosk into the device.

  Prompts time out after about 25 seconds, before BlueZ gives up on its own.

OpenFlight only answers pairing prompts for pairings started from the kiosk.
Pairing requests that start on another device still go to the desktop's own
Bluetooth agent.

## Show desktop

Some setup still needs the Raspberry Pi desktop. When OpenFlight was started
by `scripts/start-kiosk.sh` in a graphical session, the panel has an
**Advanced** tab with **Show desktop**:

1. Tap **Show desktop** and confirm. The kiosk browser closes. The server,
   radar and any simulator connections keep running.
2. Nothing reopens the kiosk automatically, so the desktop stays usable.
3. To go back, open **Return to OpenFlight** on the desktop or in the
   applications menu. Tapping the normal **OpenFlight** icon also works. The
   kiosk reopens on the running server. If OpenFlight has stopped in the
   meantime, the same icon starts it again.

The **Advanced** tab is hidden when:

- OpenFlight was started without `start-kiosk.sh`, for example with
  `openflight-server`.
- No graphical session exists (`DISPLAY` and `WAYLAND_DISPLAY` are unset).
- No kiosk browser could be launched.

The desktop launcher installer adds the **Return to OpenFlight** entry:

```bash
scripts/setup/install_desktop_launcher.sh
```

## Setup on a fresh Raspberry Pi

OpenFlight never runs as root. Wi-Fi and Bluetooth changes go through
NetworkManager and BlueZ on the system D-Bus, so the account that runs
OpenFlight needs a few specific permissions. `scripts/setup/setup.sh` offers
to grant them, or you can run the script directly:

```bash
sudo ./scripts/setup/setup_connectivity.sh --user "$USER"
# Log out and back in (or reboot) so the new groups apply.
```

The script:

| Change | Why |
|---|---|
| Installs `network-manager`, `bluez`, `polkitd`, `acl` if missing | These are the system services the panel talks to |
| Enables `bluetooth.service` | BlueZ must be running |
| Creates the `openflight-network` group and adds the user to it and to `bluetooth` | Group-based grants; BlueZ's D-Bus policy already allows `bluetooth` |
| `/etc/polkit-1/rules.d/50-openflight-network.rules` | Grants only NetworkManager's `network-control`, `wifi.scan`, `enable-disable-wifi` and `settings.modify.system` to that group |
| `/etc/udev/rules.d/70-openflight-rfkill.rules` | Adds a group ACL on `/dev/rfkill` so Bluetooth can be soft-unblocked |

No `sudoers` entries are created, and OpenFlight never runs shell commands for
connectivity. To remove the rules:

```bash
sudo ./scripts/setup/setup_connectivity.sh --uninstall
```

Raspberry Pi OS Bookworm and later use NetworkManager by default. On older
images that still use `dhcpcd`, switch in `sudo raspi-config` (*Advanced
Options → Network Config*). Until you do, the Wi-Fi tab reports that the
system service isn't running.

## Security model

OpenFlight has no user accounts: whoever is at the touchscreen is the
operator. The web server listens on every interface so other devices can
view shots, so the `/api/system/*` endpoints check each request and accept
it only when all of these hold:

- the TCP peer is a loopback address;
- the `Host` header names `localhost` or a loopback IP (this blocks DNS
  rebinding);
- any `Origin` header is a loopback origin (this blocks cross-site requests
  from other pages);
- no `X-Forwarded-*`, `Forwarded` or `X-Real-IP` header is present, so a
  reverse proxy on the Pi can't make remote clients look local.

Anything else gets `403`. Live updates (status, operation results, pairing
prompts) go to a Socket.IO room that only sockets passing the same check can
join. The request body must be JSON, which forces a CORS preflight.

Wi-Fi passwords are sent to NetworkManager once and stored by
NetworkManager. OpenFlight doesn't log them, keep them in memory after the
request, or include them in events.

## Development without hardware

```bash
uv run openflight-server --mock --connectivity mock   # simulated Wi-Fi and Bluetooth
cd ui && npm run dev                                  # http://localhost:5173
```

The mock accepts the Wi-Fi password `openflight` and the Bluetooth passkey
`123456`. *Clubhouse Guest* reports a captive portal. `--connectivity off`
disables the feature. `auto`, the default, uses NetworkManager and BlueZ on
Linux and reports "not supported" on other platforms. The Node mock server
(`npm run dev:mock`) doesn't implement these endpoints, so the controls stay
hidden there.

## Verification checklist

### Automated (CI or any Linux machine)

```bash
uv run pytest tests/test_connectivity.py tests/test_connectivity_dbus.py \
  tests/test_connectivity_setup.py tests/test_kiosk_desktop_control.py -v
cd ui && npm test && npx playwright test tests/e2e/connections.spec.ts
```

| Suite | Covers |
|---|---|
| `test_connectivity.py` | Input validation, the local-device access policy on every `/api/system` route, operation lifecycle, busy handling, password redaction, internet fallback, degraded or unsupported platforms, Socket.IO room isolation |
| `test_connectivity_dbus.py` | The real NetworkManager and BlueZ adapters against fake services on a private `dbus-daemon`: scanning, saved and new and hidden networks, wrong password (profile removed), polkit denial, BlueZ agent confirm/passkey/PIN/reject/timeout, rfkill, service not running |
| `test_connectivity_setup.py` | The polkit rule, run with Node against allowed and denied actions, plus the udev rule and setup script contract |
| `test_kiosk_desktop_control.py` | The real `start-kiosk.sh` supervisor functions: Show desktop, no automatic relaunch, Return to OpenFlight, launcher install |
| UI unit tests and `connections.spec.ts` | Indicators, panel states, password keyboard, pairing prompts, and end-to-end join, disconnect, forget and pair flows against `--connectivity mock` |

### On a Raspberry Pi (hardware required)

Run these on a fresh Raspberry Pi OS image after `setup.sh`, then log out and
back in:

1. `id` lists `openflight-network` and `bluetooth`.
   `pkaction --action-id org.freedesktop.NetworkManager.network-control --verbose`
   succeeds.
2. Start with `scripts/start-kiosk.sh`. The Wi-Fi and Bluetooth icons appear in
   the header and on the club picker.
3. From a phone on the same network, open `http://<pi>:8080`: there are no
   icons, and `curl http://<pi>:8080/api/system/connectivity` returns `403`.
4. Scan, then join a WPA2 network with a deliberately wrong password. The
   panel shows "Wrong password", and `nmcli connection show` has no new profile.
5. Join with the right password. The header shows the SSID and bars, and
   **Internet** shows *Available*.
6. Unplug the upstream router's WAN link. After about 30 seconds the panel
   shows *Local only* and shots still record.
7. Reboot. The Pi rejoins the saved network automatically. Then **Forget** it:
   `nmcli connection show` no longer lists it.
8. Join a hidden SSID with **Other network…**.
9. Turn Wi-Fi off and on from the panel.
10. `rfkill block bluetooth`, then **Turn on** in the panel: Bluetooth powers up.
11. Pair a phone (numeric comparison), a keyboard (passkey entry) and a
    speaker (just-works). Connect, disconnect and forget each one.
12. Start pairing, then cancel on the phone. The kiosk prompt closes, and
    nothing is left paired.
13. Search `journalctl -u openflight` and `~/openflight_sessions/terminal_logs/`
    for the Wi-Fi password you typed: there are no matches.
14. **Show desktop**: the kiosk closes, the desktop stays for more than a
    minute, and shots still record (check with a phone viewer). **Return to
    OpenFlight** reopens the kiosk. Repeat with OpenFlight started from the
    systemd service.
15. Stop Bluetooth with `sudo systemctl stop bluetooth`: the Bluetooth tab says
    the service isn't running, and Wi-Fi still works. Start it again: Bluetooth
    recovers without restarting OpenFlight.
