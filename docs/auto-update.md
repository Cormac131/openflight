# Automatic Updates

A Pi on a release channel downloads new releases in the background and
installs them the next time OpenFlight starts. Nothing restarts while a round
is in progress, a release that fails to start is rolled back, and only the
kiosk itself can trigger an update (never a device on the LAN). Releases are
the artifacts described in [release-process.md](release-process.md).

## Enabling it

`scripts/setup/setup.sh` asks "Enable automatic updates for this Pi?" and, on
yes, migrates the checkout, records the channel and installs a systemd timer.
By hand:

```bash
cd ~/openflight
uv run openflight-update migrate           # ~/openflight becomes a link into ~/openflight-releases/
uv run openflight-update set-channel stable   # or experimental, or off
uv run openflight-update status
```

The timer (`scripts/setup/openflight-update.timer`) runs
`openflight-update check` five minutes after boot and every six hours with up
to thirty minutes of jitter. `openflight-update check` also runs on demand from
the kiosk menu.

## How it works

```
~/openflight                 -> ~/openflight-releases/v0.3.1     the install link
~/openflight-releases/
  v0.3.1/                    the running release (.venv, ui/node_modules, ui/dist, config/sim.json)
  v0.3.0/                    kept while `previous` points at it
  source-0123456789ab/       your migrated git checkout; never deleted automatically
  staged -> v0.3.2           downloaded, verified and prepared; applied on the next start
  previous -> v0.3.0         rollback target
  pending-confirm            present between a swap and the first successful start
  .downloads/                tarballs while downloading
```

1. **check** looks up the newest release on the channel (`releases/latest`
   for stable, the newest `-dev.` pre-release for experimental). On the same
   channel only a newer version is staged; after switching channels the
   channel's latest is staged even if it sorts lower, which is how a Pi moves
   from experimental back to stable.
2. **stage** downloads `openflight-<tag>.tar.gz` and its `.sha256`, verifies
   the digest, unpacks into `~/openflight-releases/<tag>/`, reuses Electron's
   `node_modules` when `ui/package-lock.json` is unchanged (otherwise runs
   `npm ci`), builds the release's own `.venv` with `uv sync --locked`
   (mirroring a camera install's system-site-packages venv), and proves it
   with `openflight-server --version`. Only then does the `staged` link
   appear. Any failure removes the partial tree and is recorded in the status.
3. **apply** happens in `scripts/start-kiosk.sh` at the next start (or when
   "Restart to update" is tapped): it writes `pending-confirm`, copies
   `config/sim.json` across, points `previous` at the running release and the
   install link at the staged one, then relaunches itself from the new tree.
4. **confirm** clears the marker once the server answers on its port and
   prunes trees no link points at.
5. **rollback** runs when the new release fails to start: the install link
   returns to `previous`, the tag is remembered in `bad_tags` so it is never
   staged again, and the status shows `startup_failed`.

Releases published before the updater existed are refused
(`unsupported_release`): they could not confirm or roll back a swap.

### The launcher's part

`scripts/kiosk-update.sh` (sourced by `start-kiosk.sh`) owns every swap:

- Right after the single-instance lock, `openflight-update apply --if-staged`
  runs. On success the launcher drops the lock and re-executes itself from
  `~/openflight` (now the new tree) with its original arguments;
  `OPENFLIGHT_UPDATE_APPLIED=1` stops the relaunch from applying twice.
- Once the server answers on its port, `confirm` clears the pending marker.
  If startup fails first, `rollback --reason "<failure>"` runs before the
  failure is shown, and the exit lets `openflight.service` restart the
  previous release.
- When the server exits with status 75 (`openflight.update.RESTART_EXIT_CODE`,
  sent by "Restart to update"), the launcher stops the splash and the kiosk
  window, drops the lock and re-executes itself, so the apply step above runs
  again; `OPENFLIGHT_UPDATE_RESTART=1` lets it wait up to 15 s for the old
  instance's helpers to release the lock.

Any other server exit status now runs `cleanup` (closing the kiosk window)
and is passed through as the launcher's exit status.

## Commands

| Command | Purpose | Exit |
|---------|---------|------|
| `openflight-update status [--json]` | installed, staged, previous, available, last check, error | 0 |
| `openflight-update check` | look up and stage the channel's newest release | 0, 1 on staging failure |
| `openflight-update set-channel stable\|experimental\|off [--repository owner/name]` | choose what to follow | 0 |
| `openflight-update apply [--if-staged]` | swap the install link (the launcher does this) | 0, 2 when nothing is staged |
| `openflight-update confirm` | mark the swapped release as working | 0, 2 when nothing pending |
| `openflight-update rollback [--reason TEXT] [--force]` | return to `previous` | 0, 2 when nothing pending |
| `openflight-update migrate` | turn a checkout into a managed install | 0 |
| `openflight-update prune` | delete unlinked release trees and download leftovers | 0 |

All commands take `--install-link` and `--releases-root`; the environment
variables `OPENFLIGHT_INSTALL_LINK`, `OPENFLIGHT_RELEASES_ROOT`,
`OPENFLIGHT_UPDATE_CONFIG` and `OPENFLIGHT_UPDATE_STATUS` override the
defaults (`~/openflight`, `~/openflight-releases`,
`~/.config/openflight/update.json`, `~/.config/openflight/update-status.json`).
`OPENFLIGHT_GITHUB_TOKEN` raises GitHub's API limit when many checks run.

## Status states

`unmanaged` (the install link is not a symlink to the running tree; run
`migrate`), `disabled` (no channel chosen), `checking`, `downloading`,
`staging`, `staged`, `pending_confirm`, `up_to_date`, `held_back` (the newest
release failed here before), `failed` (see `error`: `offline`,
`rate_limited`, `not_found`, `checksum`, `archive`, `disk`, `deps`,
`unsupported_release`, `startup_failed`).

## Disk and network

Three release trees (current, previous, staged) plus a download: budget about
2 GB. `check` refuses to stage when free space is below three times the
tarball plus 1 GB. Checks fail closed offline and never block startup.

## Troubleshooting

- **A release keeps failing to start**: `openflight-update status` shows
  `startup_failed` and the tag in `bad_tags`; fix the cause, then
  `openflight-update check` after a newer release exists.
- **Roll back by hand**: `openflight-update rollback --force --reason "manual"`
  (needs a `previous` link), then restart OpenFlight.
- **Undo migration**: stop OpenFlight, `rm ~/openflight`, `mv
  ~/openflight-releases/source-<sha> ~/openflight`, then in that directory
  `uv sync --reinstall-package openflight`.
- **Migration failed on `uv sync`**: run `uv sync --reinstall-package
  openflight` inside `~/openflight-releases/source-<sha>/` and retry.
- **Timer state**: `systemctl list-timers openflight-update.timer`,
  `journalctl -u openflight-update`.
