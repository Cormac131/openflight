# Profiles (replacing Players)

**Date:** 2026-08-27
**Status:** Approved design, ready for implementation planning

## Problem

Today a "player" is a bare name string. `usePlayerStore` keeps a list of names and a
selected name in browser `localStorage`; the socket contract is `set_player` →
`player_changed` carrying `player_name`; the server holds a single global
`current_player_name`; and every `Shot` and swing-speed event is stamped with
`player_name` and filtered by case-insensitive name match.

Three problems follow from that model:

1. **A name is not an identity.** Renaming is impossible without orphaning every shot
   already recorded under the old name.
2. **The roster is browser-local.** Nothing server-side can read it, so no future feature
   can attach settings to a profile. A reflashed kiosk or a second browser loses the roster.
3. **Two sources of truth race.** `session_state.player_name` (a connect-time snapshot) and
   `player_changed` (a live update) can disagree. `ui/src/services/playerSocketSync.ts`
   exists solely to referee that race, and `App.tsx:131-153` runs a reconciliation dance
   for the same reason.

"Player" is also the wrong word. The thing shots are attributed to may be a person *or* a
place — a range bay, a home net, a course — and the current noun excludes half of that.

## Goals

- Replace "player" with "profile" across the UI and socket layer.
- Give each profile a stable id, so renaming never orphans shots.
- Persist profiles server-side, so later features can attach settings to them.
- Collapse the two sources of truth into one, deleting the reconciliation code.

## Non-goals

- Defining any specific profile setting. `settings` is an open dict; later features claim
  keys in it. No setting is specified or consumed by this work.
- Renaming anything in `src/openflight/sim/` or `src/openflight/gspro/`. `PlayerState`
  and GSPro's `Player` fields are an external wire protocol, not our terminology.
- Cloud sync of profiles.
- Migrating existing player data. This is a clean break (see Migration).

## Data model

A profile record is untyped — a profile is just a name, whether it denotes a person or a
place:

```json
{
  "id": "a3f2c1d0e5b6478f9a0b1c2d3e4f5061",
  "name": "Home Range",
  "created_at": "2026-08-27T10:14:03Z",
  "settings": {}
}
```

- `id` — uuid4 hex, generated, never derived from the name. Renames are free.
- `name` — trimmed, capped at 40 characters. **Not unique**; `id` is the key, so two
  profiles named "Range" are legal.
- `created_at` — ISO 8601 UTC.
- `settings` — an open dict the server round-trips untouched. This is the extension point
  for later features; nothing in this work reads or writes it.

### Store

File: `~/.config/openflight/profiles.json` (the established config dir, alongside
`cloud/config.py`'s `cloud.json` and the camera exposure state).

```json
{ "profiles": [ ... ], "active_profile_id": "a3f2..." }
```

New module `src/openflight/profiles.py` owning a `ProfileStore` class:

| Method | Behaviour |
|---|---|
| `list()` | All profiles, insertion-ordered |
| `get_active()` | The active profile record |
| `add(name)` | Append and make active; returns the new record |
| `rename(id, name)` | Change `name` in place; `id` and shot attribution unaffected |
| `remove(id)` | Delete; **rejected** if `id` is active or the last profile |
| `set_active(id)` | Change `active_profile_id`; rejected if `id` is unknown |

Persistence details:

- **Atomic writes** — write to a temp file in the same directory, then `os.replace`. A
  power cut on the Pi mid-write must not truncate the roster.
- **Corrupt or missing file** — log and fall back to a freshly seeded store containing one
  default profile. Never raise into server startup.
- **Concurrency** — a single in-process lock. This is a kiosk with one writer; no file
  locking.
- **Roster cap** — 12 profiles, matching today's limit.

### Invariants

- At least one profile always exists.
- `active_profile_id` always names a live profile.
- Rejected mutations change nothing and are answered with the unchanged state.

### Shot attribution

`Shot` and `SwingSpeedEvent` gain `profile_id` and `profile_name` and drop `player_name`.

- `profile_id` is the filter key, matched **exactly** — no case folding. The existing
  `normalizePlayerName` (`ui/src/types/shot.ts:157`) and `_normalize_player_name` /
  `_player_matches` (`server.py:2144-2152`) are deleted outright. Case-insensitive name
  matching is a bug source: two profiles differing only in case currently collide.
- `profile_name` is a denormalized snapshot taken at stamp time, so session JSONL stays
  human-readable without joining against `profiles.json`. It is never used for filtering.

## Socket contract

### Server → client

One authoritative snapshot event, emitted on connect and after **every** mutation
(including rejected ones):

```
"profiles"  { profiles: [{id, name, created_at, settings}], active_profile_id }
```

Roster and selection always arrive together and therefore cannot disagree. `session_state`
drops `player_name` and carries no selection at all — this is what removes the race.
`ui/src/services/playerSocketSync.ts` and its test are **deleted**, not renamed.

`session_cleared` becomes `{ profile_id, shots }`.

### Client → server

```
"set_active_profile"  { profile_id }
"add_profile"         { name }                 → adds and makes active
"rename_profile"      { profile_id, name }
"remove_profile"      { profile_id }
"clear_session"       { profile_id }
```

The four roster mutations (`set_active_profile`, `add_profile`, `rename_profile`,
`remove_profile`) each end by broadcasting the `profiles` snapshot. `clear_session` does not —
it mutates shots, not the roster — and answers with `session_cleared` instead.

### Rejected input

Unknown `profile_id`, blank name, removing the active profile, and removing the last
profile are all answered with the unchanged snapshot rather than a silent default or an
error event. A confused or stale client self-heals on the next round trip.

Note that the server rejecting `remove_profile` on the active id is the backstop for the
UI rule below — the invariant holds even if a stale client asks.

## Server changes

- `current_player_name` (`server.py:90`) is replaced by the `ProfileStore`.
- `shot.player_name = current_player_name` (`server.py:3125`) becomes `profile_id` /
  `profile_name` stamped from `store.get_active()`. Same for the swing-speed event path
  (`server.py:3782`).
- `_normalize_player_name`, `_player_matches`, and `_clear_player_rows`
  (`server.py:2144-2187`) collapse into `_clear_profile_rows(profile_id)` doing an exact
  id match.
- `handle_set_player` (`server.py:2116`) is replaced by the five handlers above.
- `session_logger` field names follow the `Shot` rename.
- `sim/` and `gspro/` are untouched.

## UI changes

### Store

`usePlayerStore` → `useProfileStore`, and it **stops being a source of truth**. It holds
`profiles`, `activeProfileId`, and actions that emit socket events; the `profiles` snapshot
handler replaces state wholesale.

**No `localStorage`.** The server already tracks the active profile globally, exactly as it
does today with `current_player_name`, so a browser-side copy is a second truth with
nothing to add. Before the socket connects the roster renders a disabled skeleton rather
than a guessed default. Actions no-op while disconnected.

This deletes `App.tsx:131-153` entirely: the `appliedServerPlayer` tracking, the
echo-on-connect, and the comment explaining why it must not re-emit on change.

### Behaviour

- **Deleting the active profile is not allowed.** The ✕ stays hidden on the active card
  and the server rejects it. Deleting the profile whose shots are on screen is a usability
  trap, not a capability.
- **Renaming is added.** Stable ids make it safe for the first time. The add dialog is
  reused with an initial value, wired to `rename_profile`.

### Renames

| From | To |
|---|---|
| `PlayersPanel` | `ProfilesPanel` |
| `AddPlayerDialog` | `AddProfileDialog` |
| `players-panel__*` CSS | `profiles-panel__*` |
| `'players'` panel view / tab | `'profiles'` |
| `filterShotsByPlayer` / `excludeShotsByPlayer` | `filterShotsByProfile` / `excludeShotsByProfile`, keyed on `profileId` |
| `SwingSpeedStatsFilter.playerName` | `SwingSpeedStatsFilter.profileId` |
| `socketService.setPlayer` / `clearSession(playerName)` | `setActiveProfile` / `clearSession(profileId)` |

All four locale files (`en`, `es`, `fr`, `pt`) get the key renames **plus real
translations** — Perfiles / Profils / Perfis. Affected keys: `nav.players`,
`players.rosterAria`, `players.shots`, `players.shot`, `players.namePlaceholder`,
`menu.player`, `menu.addPlayer`, `menu.removePlayer`, `shots.colPlayer`,
`metric.playerImplement`, `clearSession.detail`.

The mock server (`ui/mock-server/`) implements the same profile events over an in-memory
store, so `--mock` keeps working.

## Migration

**Clean break.** No migration of existing player data.

- On first run the server seeds `profiles.json` with a single profile named `Profile 1`.
- Existing browser `localStorage` player rosters (`openflight-players`,
  `openflight-selected-player`) are simply abandoned in place on existing kiosks, not actively
  removed. Adding removal code would itself be the migration cruft the clean break set out to
  avoid.
- Old session JSONL entries keep their `player_name` field and are simply not filterable by
  profile. Nothing reads them at runtime.
- The socket exposes only the new events. No dual-emit compatibility window, so there is no
  cruft to remember to delete.

## Testing

### Python

New `tests/test_profiles.py` — `ProfileStore` directly:

- Missing file seeds a default profile.
- Corrupt JSON falls back to a seeded default rather than raising.
- Atomic write leaves no truncated file on failure.
- Name trimmed and capped at 40 characters.
- Blank name rejected.
- Duplicate names allowed.
- `remove` of the active id rejected.
- `remove` of the last profile rejected.
- `set_active` / `rename` / `remove` with an unknown id are no-ops.
- `settings` round-trips byte-identical through save/load — the guarantee later features
  depend on.
- `active_profile_id` always names a live profile after any operation.

Additions to `tests/test_server.py`:

- Every mutation handler broadcasts the `profiles` snapshot.
- A rejected mutation broadcasts the **unchanged** snapshot.
- Shots stamp `profile_id` and `profile_name` from the active profile.
- Swing-speed events stamp the same fields.
- `clear_session` scopes by exact id, including two profiles whose names differ only in
  case — the case the old code got wrong.
- `session_state` carries no selection field.

### UI (vitest)

- `useProfileStore.test.ts` — snapshot replaces state wholesale; actions emit the right
  events with the right payloads; actions no-op while disconnected.
- `shot.test.ts` — id-keyed filtering, including shots with a missing or unknown
  `profile_id` falling out of every profile.
- `ProfilesPanel.test.tsx` — roster render, shot counts, select, rename, ✕ hidden on the
  active card.
- `AddProfileDialog.test.tsx` — add and rename modes.
- `i18n.test.ts` — existing key-parity check catches any missed locale key.
- `App.test.tsx` — reconciliation tests deleted; one added for the pre-connect skeleton.
- E2E `app.spec.ts` and `helpers.ts` updated for the new panel and tab.

Implementation is test-first per the project's rules.

### Verification

```
uv run pytest tests/ -v
uv run pylint src/openflight/ --fail-under=9
uv run ruff check src/openflight/ && uv run ruff format --check src/openflight/
cd ui && npm run lint && npm test
```

plus the Playwright e2e run.

## Decisions and rationale

| Decision | Rationale |
|---|---|
| Untyped profile (no `person`/`location` kind) | YAGNI. A name is a name; `settings` can differentiate later without a schema enum to maintain. |
| Server-owned JSON store | Later features attaching settings to profiles are mostly server-side concerns. A reflashed kiosk or second browser should not lose the roster. |
| Stamp both `profile_id` and `profile_name` | Id makes renames safe; the denormalized name keeps session JSONL readable without a join. |
| One `profiles` snapshot event | Roster and selection cannot disagree. Deletes the race `playerSocketSync.ts` was written to referee. Cost is the full roster on the wire per change — a rounding error at 12 small records on a LAN socket. |
| No `localStorage` | The server is already globally authoritative for the active profile. A browser copy is a second truth with nothing to add. |
| Delete-active forbidden | Discarding the shots currently on screen is a usability trap. |
| Rename included | It is the concrete payoff of stable ids and the reason to do this rather than a find-and-replace. |
| Clean break, no migration | Single-deployment DIY project; a compat window would be cruft with no consumer. |
