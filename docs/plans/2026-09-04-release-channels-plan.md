# Stage 1: Stable + Experimental Release Channels

## Context

OpenFlight is deployed by `git clone` onto Raspberry Pis and started via
`scripts/start-kiosk.sh`, which runs `uv sync` (unpinned, `uv.lock` is
gitignored) and builds `ui/dist` only when it is missing. There are **zero git
tags** on the fork or upstream, no release workflow, and the version is
duplicated in `pyproject.toml:3` and `src/openflight/__init__.py:3` (both
`0.2.0`) with an unrelated `ui/package.json` `1.0.0`. The version is never
shown to the user: it only reaches the session log (`session_logger.py:173`)
and the cloud manifest (`cloud/filtering.py:21`). `docs/CHANGELOG.md` is
Keep-a-Changelog with a 330-line `[Unreleased]` section (duplicated `###`
headings) and compare links that point at the old `jewbetcha/openflight` org
and at tags that do not exist.

This is Stage 1 of three:

1. **Release channels (this plan)**: versioned, downloadable, machine-readable
   releases on two channels, announced on Discord, and a device that knows
   what it is running.
2. **Auto-update**: an on-device updater that follows a channel. PR #260's
   `docs/electron-kiosk-shell.md` sketches the shape (main-process driven,
   never triggerable over the LAN, build-then-swap, idle-gated, fail-closed).
3. **Pi images**: one image per channel with a release preinstalled.

Stage 1 produces exactly what stages 2 and 3 consume, and nothing more: a
tag per release, a downloadable artifact with a prebuilt UI, a `release.json`
identity file, and a runtime that reads it.

### Assumed already merged: PR #260 (`feat/electron-react-shell`)

Adds the Electron kiosk shell (`ui/electron/main.js`), `scripts/require-node.sh`
(Node >= 22.12), `scripts/kiosk-browser.sh`, `scripts/ensure-kiosk-ui.sh`
(builds `ui/dist` when missing, tries `npm install` for Electron, falls back to
Chromium), a `/tmp/openflight-kiosk-<port>.lock` guard (exit 3), systemd
`StartLimitBurst`/`RestartPreventExitStatus=3`, `.gitattributes` (`*.sh text
eol=lf`), `tests/test_start_kiosk.py` and `tests/test_openflight_service.py`.
This plan builds on it: the release artifact ships a prebuilt `ui/dist` so
`ensure_kiosk_ui` skips the on-Pi build, `.gitattributes` gains
`export-ignore` lines, and `release.json` is the identity Stage 2's updater
refreshes.

## Decisions confirmed with the user

| # | Decision | Choice |
|---|----------|--------|
| 1 | Review mode | BIG CHANGE (section-by-section review) |
| 2 | Experimental cadence | Automatic pre-release on every push to `main` |
| 3 | Python dependency pinning | Commit `uv.lock` (prerequisite PR 0) |
| 4 | Channel surface in Stage 1 | Read-only version + channel line; no switcher until Stage 2 |
| 5 | Announcements | Release pipeline posts to Discord (both channels) |

## Channel model

- One long-lived branch, `main`. No `stable` branch; hotfixes branch from the
  stable tag, cherry-pick, tag the next patch, merge back.
- **stable**: a maintainer pushes tag `vX.Y.Z` on a `main` commit. The stable
  workflow verifies it, runs the full test suites, publishes a GitHub Release
  and marks it *latest* only if it is the highest stable tag (so a `v0.3.1`
  hotfix cut after `v0.4.0` never becomes latest).
- **experimental**: every push to `main` publishes a GitHub *pre-release*
  tagged `vX.Y.Z-dev.<N>` where `X.Y.Z` is the base version in `__version__`
  and `N = git rev-list --count HEAD` (deterministic, monotonic on `main`,
  reproducible from git alone). Semver orders `v0.3.0-dev.42 < v0.3.0`, so an
  experimental device is always "behind" the stable it precedes. Older
  experimental pre-releases are pruned (keep the newest 10).
- Runtime channel values: `stable`, `experimental`, `source` (a git checkout
  not produced by a release, i.e. every Pi installed via `git clone` today and
  every dev machine). Source builds identify as `0.3.0+<shortsha>` (valid
  semver build metadata and valid PEP 440 local version).
- "experimental" already means other things in this codebase (`Shot.quality`,
  `--experimental-kld7-*`, `experimental_*` session fields). The user chose
  the name knowingly. In code the value only ever appears as
  `ReleaseInfo.channel`; docs and log lines say "release channel".

## PR plan (each within the 50-file / 2000-line anti-slop ceilings)

| PR | Title | Contents |
|----|-------|----------|
| 0 | `build: commit uv.lock and check it in CI` | un-ignore + commit `uv.lock`, `uv lock --check` job, Dependabot `pip` → `uv`. Label `anti-slop-exempt` (lockfile exceeds 2000 lines) + `no-tests-needed`. |
| A | `build(release): single version source and release identity` | hatch dynamic version, `release.py`, server/CLI/cloud/session-log exposure, UI version line, tests. |
| B | `ci(release): stable and experimental release workflows` | `prepare_release.py`, `build_artifact.py`, `discord_payload.py`, composite Discord action, two workflows, reusable CI, `.gitattributes export-ignore`, docs, bug template. |
| C | `docs(changelog): consolidate Unreleased and fix compare links` | `prepare_release.py normalize` output, hand-reviewed. `no-tests-needed`. |
| D | `chore(release): v0.3.0` then `chore: start 0.4.0 development` | produced by the script; first stable tag. |

## PR 0: commit `uv.lock`

- `.gitignore:31`: remove `uv.lock` (keep `.uv/`). Run `uv lock`, commit.
- `.github/workflows/pytest.yml`: add job `lock` (`astral-sh/setup-uv`,
  `uv lock --check`).
- `.github/dependabot.yml`: `package-ecosystem: pip` → `uv` (verify the
  ecosystem name against current Dependabot docs when opening the PR).
- `CONTRIBUTING.md`: "run `uv lock` after changing dependencies".
- `scripts/start-kiosk.sh` needs no change: `uv sync` honours a committed
  lock. Note `uv.lock` records the project version, so version bumps must be
  followed by `uv lock`; the CI check catches omissions.

## PR A: runtime identity

### Single version source
- `src/openflight/__init__.py:3` keeps `__version__` as the base version
  (plain `X.Y.Z`, enforced by test).
- `pyproject.toml`: remove `version = "0.2.0"`, add `dynamic = ["version"]`
  and `[tool.hatch.version] path = "src/openflight/__init__.py"`. Fix
  `[project.urls]` to `open-flight/openflight`.
- `ui/package.json` version stays `1.0.0` (private, never published).
- `.gitignore`: add `release.json` (dev checkouts must never commit one).

### `src/openflight/release.py` (new)
- Constants: `RELEASE_FILE_NAME = "release.json"`, `RELEASE_FORMAT_VERSION = 1`,
  `DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]` (same root as
  `server.py:57`), `RELEASE_CHANNELS = ("stable", "experimental")`,
  `SOURCE_CHANNEL = "source"`.
- `@dataclass(frozen=True) class ReleaseInfo`: `version`, `base_version`,
  `channel`, `tag`, `commit`, `built_at`, `repository` (all but the first
  three `Optional[str]`); `label` property → `"0.3.0-dev.42 (experimental)"`;
  `to_dict()` adds `format_version`.
- `source_release_info(repo_root, base_version=__version__)`: version
  `base+<sha>` when `_git_short_head` succeeds, else `base`; channel `source`.
- `_git_short_head(repo_root)`: only runs when `<root>/.git` exists (dir or
  worktree file) and `git` is on PATH; `subprocess.run(["git","rev-parse",
  "--short=12","HEAD"], timeout=5)`; any exception → `None`.
- `load_release_info(repo_root, base_version=__version__)`: reads
  `release.json`; `_parse_release_file` returns `None` on any shape error
  (not an object, missing `version`/`channel`/`tag`, channel not in
  `RELEASE_CHANNELS`, `base_version != __version__` i.e. a stale file next to
  newer code); optional fields default to `None`, unknown keys ignored. Any
  failure logs one warning and returns `source_release_info`. Never raises.
- `get_release_info()`: `functools.lru_cache(maxsize=1)` wrapper; tests call
  `cache_clear()` via an autouse fixture.
- `release.py` is the single writer and reader of the `release.json` schema:
  `scripts/release/build_artifact.py` imports it to write the file.

### Consumers
- `server.py` `handle_connect` (`:2332`): emit `release_info`
  (`get_release_info().to_dict()`) before the `if monitor:` block, so the
  version shows in every mode (`session_state` is only emitted when a monitor
  exists). `main()` (`:5016`): `--version` action printing
  `openflight-server <label>`; log the label once at startup.
- `session_logger.py:17,173`: `app_version = info.version`, new
  `SessionMetadata.app_channel = info.channel` (additive, no format bump).
- `cloud/filtering.py:21`: `CLIENT_VERSION = get_release_info().version`
  (signatures unchanged). `cloud/commands.py` `cmd_status` (`:226`): print
  `Client:     <label>` after `Endpoint:`.
- UI: new `ui/src/types/release.ts` (`ReleaseChannel`, `ReleaseInfo`);
  `useSystemStore` gains `releaseInfo | null` + `setReleaseInfo`;
  `socketService.ts` listens for `release_info` next to `power_status`
  (`:91`); `MenuSheet.tsx` System section gets a `menu-sheet__status-row`
  (existing CSS `panel.css:1251-1265`) with label `t('menu.version')` and
  value `<version> · <channel label>` or `t('menu.unavailable')`; i18n keys
  `menu.version`, `menu.channelStable`, `menu.channelExperimental`,
  `menu.channelSource` in `en/es/fr/pt.ts`; `ui/mock-server/session.ts`
  `releaseInfo()` sample and `handlers.ts:28` emits it on connect.

## PR B: release pipeline

### `scripts/release/prepare_release.py` (new; pure functions + thin argparse; validate everything before writing; writes both files or none)
- `release X.Y.Z [--date] [--repo-url]`: set `__version__`; roll
  `[Unreleased]` into `## [X.Y.Z] - date`; insert a fresh empty
  `## [Unreleased]`; merge duplicate `###` headings in Keep-a-Changelog order
  (Added, Changed, Deprecated, Removed, Fixed, Security, then unknown in
  first-seen order); regenerate the whole link-reference block for
  `https://github.com/open-flight/openflight` (oldest gets `releases/tag/`).
  Errors: no `[Unreleased]`, empty Unreleased, `[X.Y.Z]` already present,
  not greater than newest, invalid version, `__version__` line not found.
- `next X.Y.Z`: set `__version__` only; refuse if not greater than current.
- `check X.Y.Z`: exit 1 unless `__version__ == X.Y.Z` and `## [X.Y.Z]`
  exists (used by the stable workflow's `verify` job).
- `notes X.Y.Z`: print the section body (used for release notes).
- `normalize`: heading merge + link regeneration without releasing (PR C).
- Prints next steps (`uv lock`, commit, tag) after `release`/`next`.

### `scripts/release/build_artifact.py` (new)
`--tag --channel --repository --output [--repo-root --commit --built-at]`.
Validates `-dev.` iff experimental and that `ui/dist/index.html` exists.
`git archive --prefix=openflight-<tag>/ <commit>` into a temp dir, copy
`ui/dist`, write `release.json` via `ReleaseInfo.to_dict()`, produce
`openflight-<tag>.tar.gz` + `.tar.gz.sha256` (`sha256sum -c` format).
Excludes are declarative in `.gitattributes` (`export-ignore` for `models/`,
`cad/`, `session_logs/`, `archive/`, `plans/`, `docs/plans/`, `docs/prs/`,
`docs/superpowers/`, `docs/assets/`, `docs/*.pdf`, `docs/*.html`, `.github/`);
`firmware/` stays in (the Pi flashes from `firmware/releases/`). This also
shrinks GitHub's automatic "Source code" assets.

### `scripts/release/discord_payload.py` (new)
`--title --tag --channel --release-url --artifact --notes-file --output`.
Emits `{"content": ..., "allowed_mentions": {"parse": []}}` (blocks
`@everyone` smuggled through commit subjects). Layout: `**OpenFlight vX** ·
channel`, release link, artifact name, blank line, notes. Truncation budget =
2000 minus header minus a fixed `…\n_Full notes on GitHub._` suffix, cut at
the last newline inside the budget, final hard slice guarantees `<= 2000`.

### `.github/actions/announce-discord/action.yml` (new local composite)
Inputs `webhook-url`, `payload-file`, `dry-run`. Dry run prints the payload
and exits 0. Empty URL → `::warning::` and exit 0 (forks). `curl -sS -f
--retry 2 --max-time 20 --data @payload` ; failure → `::warning::` only. The
calling step also sets `continue-on-error: true`, so nothing after a
published release can fail the job. The secret is never echoed.

### Reusable CI
`pytest.yml` and `ui-build.yml` gain `workflow_call` triggers (and
`ui-build.yml` an `upload-dist` input that uploads `ui/dist` as artifact
`ui-dist`), so the release jobs gate on the *same* test runs and download the
exact `ui/dist` that was tested rather than rebuilding it. Their `push: main`
triggers are removed: `release-experimental.yml` is main-branch CI, so each
push runs the suites once. `pull_request` triggers stay; `pylint.yml` is
untouched. Check names on `main` change to `Experimental release / Tests /
...`; update any branch-protection rule keyed on the old names.

### `.github/workflows/release-experimental.yml` (new)
- `on: push: branches: [main]` + `workflow_dispatch` (`dry_run`, default
  true). `permissions: contents: write`. `concurrency: {group:
  release-experimental, cancel-in-progress: true}`.
- Jobs `tests` and `ui` via `uses: ./.github/workflows/...`, then `release`:
  1. `actions/checkout` (fetch-depth 0), `astral-sh/setup-uv`, `uv sync --locked`.
  2. Resolve tag: `base=__version__`; if `refs/tags/v<base>` exists → error
     telling the maintainer to merge `prepare_release.py next <version>`
     (a dev build of an already-released base would sort *before* stable).
     `tag=v<base>-dev.$(git rev-list --count HEAD)`. If a published release
     with that tag exists → `skip=true` (re-run of the same commit). If a
     stale *draft* with that tag exists → delete it. Find `prev` = newest
     published `-dev.` release for notes.
  3. Download `ui-dist`, run `build_artifact.py --channel experimental
     --repository "$GITHUB_REPOSITORY" --commit "$GITHUB_SHA"`, upload the
     `dist/release` folder as a run artifact (dry runs inspect this).
  4. Publish (not in dry run): `gh release create "$TAG" dist/release/*
     --draft --prerelease --target "$GITHUB_SHA" --generate-notes
     --notes-start-tag "$prev"`, then `gh release edit --draft=false`.
     Draft-first means GitHub creates the git tag only on publish, so a
     cancelled or failed run leaves a draft and no tag.
  5. Prune (`continue-on-error`): all `-dev.` drafts plus published `-dev.`
     releases beyond the newest 10 → `gh release delete --cleanup-tag --yes`,
     each failure a warning.
  6. Discord: `discord_payload.py` with the GitHub-generated notes, then the
     composite action with `secrets.DISCORD_EXPERIMENTAL_WEBHOOK_URL ||
     secrets.DISCORD_RELEASE_WEBHOOK_URL`; the action logs which secret it
     used.
- Uses the runner's built-in `gh` (no third-party release action).

### `.github/workflows/release-stable.yml` (new)
- `on: push: tags: ['v[0-9]+.[0-9]+.[0-9]+']` + `workflow_dispatch`
  (`tag`, `dry_run`). `permissions: contents: write`. `concurrency:
  release-stable-<tag>`, no cancel.
- Job `verify` (before any tests run): tag matches `^v\d+\.\d+\.\d+$`;
  `prepare_release.py check <version>` (`__version__` == tag and CHANGELOG
  has the section); no published release with that tag; warn (not fail) if
  the commit is not on `origin/main` (hotfix).
- Jobs `tests`, `ui` (reusable, `upload-dist`).
- Job `release`: checkout the tag, download `ui-dist`, `build_artifact.py
  --channel stable`, `prepare_release.py notes <version> > notes.md`, upload
  run artifact; publish (not in dry run): compute `latest` = tag is the
  highest stable tag by `sort -V`; `gh release create --draft --title
  "OpenFlight vX.Y.Z" --notes-file notes.md --latest=<bool>` then `edit
  --draft=false`; Discord via `DISCORD_RELEASE_WEBHOOK_URL` with the
  CHANGELOG excerpt.

### Docs and templates
- `docs/release-process.md` (new): channel model and tag scheme; cutting a
  stable (`prepare_release.py release X.Y.Z` → `uv lock` → PR
  `chore(release): vX.Y.Z` → merge → `git tag vX.Y.Z <merge-sha> && git push
  origin vX.Y.Z` → immediately `prepare_release.py next` PR); hotfix flow;
  artifact contents/excludes and the `release.json` schema; Discord secret
  setup (Discord Server Settings → Integrations → Webhooks → New Webhook →
  Copy URL; GitHub repo Settings → Secrets → Actions →
  `DISCORD_RELEASE_WEBHOOK_URL`, optional `DISCORD_EXPERIMENTAL_WEBHOOK_URL`;
  rotate by regenerating in Discord); dry-run testing; recovery (tag/version
  mismatch → delete tag, fix, retag; stale drafts are pruned automatically);
  how Stages 2 and 3 consume the outputs.
- `README.md`: "Versions and release channels" bullet + mention of the
  Menu → System version line. `CONTRIBUTING.md`: link the doc; `[Unreleased]`
  entries roll into the next stable; `uv lock` reminder.
- `.github/ISSUE_TEMPLATE/bug_report.yml`: required `version` input ("from
  Menu → System, or `uv run openflight-server --version`").
- `Makefile`: `release-check` target.

## PR C and PR D
- PR C: run `prepare_release.py normalize` on `docs/CHANGELOG.md`, review the
  merged Unreleased text by hand (it becomes the 0.3.0 release notes).
- PR D: `prepare_release.py release 0.3.0`, `uv lock`, merge, tag `v0.3.0`,
  push the tag, then a `prepare_release.py next 0.4.0` PR right away.

## Tests

Python (`tests/`, flat pytest, existing patterns):
- `tests/test_release.py` (new): `release.json` valid stable / valid
  experimental / absent / malformed JSON / top-level list / missing `tag` /
  bad channel / `base_version` mismatch / optional fields absent / unknown
  keys ignored, each failure asserting one `caplog` warning and a `source`
  result. Git fallback with a temp repo (`commit` equals `git rev-parse
  --short=12 HEAD`), no `.git` → `None`, worktree-file `.git` still
  attempted, `shutil.which` → `None`, `subprocess.run` raising
  `OSError`/`TimeoutExpired` → `None`. `version` is `base+commit` only when
  the commit is known. `label`, `to_dict` (`format_version`), cache
  behaviour (autouse `cache_clear`).
- `tests/test_server.py`: `handle_connect` emits `("release_info", dict)`
  with `get_release_info` monkeypatched (emit-collector pattern at
  `:105-125`); `--version` exits 0 and prints label (`capsys`).
- `tests/test_session_logger.py:710`: `app_version`/`app_channel` from
  `get_release_info()`. `tests/test_cloud_commands.py`: `cmd_status` prints
  `Client:`. `tests/test_cloud_filtering.py`: `CLIENT_VERSION` equals
  `get_release_info().version`.
- `tests/test_project_metadata.py`: no static `project.version`; `"version"`
  in `project.dynamic`; hatch path; `__init__.py` has exactly one
  `__version__ = "X.Y.Z"` line.
- `tests/test_prepare_release.py` (new; `sys.path` pattern from
  `tests/test_club_path_report.py:7`): every subcommand and every error case
  on tmp copies; atomicity (neither file changes on error); heading-merge
  order and item order; unknown heading preserved; first-release link uses
  `releases/tag/`; `notes` stops before the next `## ` and before link refs;
  `--date`; `check` against a copy of the real CHANGELOG head.
- `tests/test_build_artifact.py` (new; skip without `git`): temp repo with
  `export-ignore`, prebuilt `ui/dist/index.html`; tarball members (prefix,
  `src/`, `ui/dist/index.html`, `release.json`, excluded dir absent);
  `release.json` round-trips through `load_release_info`; sha256 matches
  `hashlib`; errors on missing `ui/dist`, tag/channel mismatch, unknown
  commit. Plus a text test that the real `.gitattributes` export-ignores
  `models/`, `cad/`, `session_logs/`.
- `tests/test_discord_payload.py` (new): required fields; `len(content) <=
  2000` for notes of length 0/1500/1990/5000/50000 with and without
  newlines; truncation marker only when truncated; `allowed_mentions.parse
  == []`; unicode; `@everyone` stays literal.
- `tests/test_release_workflows.py` (new, text-level, a handful of
  assertions): `permissions: contents: write`, `concurrency`,
  `continue-on-error: true` on the announce steps, the stable tag glob, the
  experimental guard message.

UI (vitest + Playwright):
- `MenuSheet.test.tsx`: `useSystemStore.setState({ releaseInfo })` for
  experimental → html contains version and "Experimental"; `null` →
  "Unavailable"; `source` → "Source checkout"; state reset after.
- `i18n.test.ts` parity covers the new keys automatically.
- `ui/tests/e2e/app.spec.ts`: one assertion that the menu shows the mock
  server's version line (only end-to-end coverage of `socketService` wiring).

## Edge cases handled
- Tag on a non-main commit: allowed (hotfix), warning only.
- Tag/version mismatch or missing CHANGELOG section: `check` fails in
  `verify` before tests; nothing created.
- Experimental when `v<base>` exists: hard error naming the fix.
- Forks: no repository guard; `release.json.repository` records the origin;
  missing Discord secret → warning + skip.
- `release.json` missing (dev checkout, `git clone` Pi): `source`,
  `0.3.0+<sha>`. Tarball on a Pi with no `.git`: file present, git never
  called. Worktree `.git` file, git absent, "dubious ownership": `commit`
  `None`, never raises. Stale `release.json`: `base_version` mismatch →
  source + warning.
- Ordering: `dev.N` is numeric, so semver and `sort -V` order `dev.9 <
  dev.10`; GitHub "latest" ignores prereleases; Stage 2 must sort
  experimental releases by `N`, not `created_at`.
- Concurrency: `cancel-in-progress` + draft-first → cancelled run leaves a
  draft and no tag; next run deletes its own stale draft, prune deletes the
  rest. Re-run on a published commit → `skip=true`, exit 0.
- Prune/API failures: non-fatal, per-tag warnings; ~10 deletions per run.
- Tarball: `export-ignore` keeps out ~47 MB models, ~19 MB CAD, ~26 MB session
  logs, PDFs; `.venv`/`node_modules` are untracked so never archived.
- No bot-created PRs (they would trip `pr-checks.yml`); humans open the
  `next` PR. Keep YAML comments minimal (25 added-comment-line ceiling).
- Discord: content capped by construction; mentions disabled; step never
  fails the job; secret never echoed.

## How Stages 2 and 3 consume this (concepts only)

**Stage 2, auto-update** (per `docs/electron-kiosk-shell.md` design A):
reads `release.json` for `channel` and `repository`; a new
`~/.config/openflight/update.json` (pattern `cloud/config.py`) stores the
channel preference, defaulting to the installed channel (`source` disables
auto-update and shows a git hint). Stable: `GET
/repos/{repository}/releases/latest`; experimental: list releases, filter
`prerelease && tag =~ -dev\.`, pick max `N`. Compare with
`ReleaseInfo.version`, download `openflight-<tag>.tar.gz` + `.sha256`,
verify, extract to a sibling `openflight-<tag>/`, `uv sync --locked`
(deterministic thanks to PR 0), swap a `current` symlink, relaunch via
`app.relaunch()`/systemd. Prebuilt `ui/dist` means no `npm run build` on the
Pi. Runs from the Electron main process or an `openflight-update` CLI it
invokes, never over Flask/socket; refuses mid-session; fails closed offline.
The `release_info` event already gives the UI what it needs for an "update
available" line and the channel switcher.

**Stage 3, Pi images:** `release-image.yml` on `release: published` (and
manual) builds with `rpi-image-gen`/`pi-gen`: Raspberry Pi OS 64-bit + apt
deps from `docs/raspberry-pi-setup.md` + Node 22 + `uv` + the release tarball
extracted to `/home/openflight/openflight` + `uv sync --locked` + Electron
`npm ci` + `openflight.service` enabled + `update.json` with the channel.
`openflight-<tag>-<channel>.img.xz` + sha256 attach to the same GitHub
Release and are announced through the same composite action.

## Verification

Per PR: `uv run pytest tests/ -v`, `uv run pylint src/openflight/
--fail-under=9`, `uv run ruff check src/openflight/`, `uv run ruff format
--check src/openflight/`; in `ui/`: `npm run lint`, `npm run format:check`,
`npm run test`, `npm run build`, `npm run test:e2e`.

Live rehearsal on the fork (`Cormac131/openflight`) before the upstream merge:
1. Merge PR B into the fork's `main` (workflow files must be on the default
   branch for `workflow_dispatch`; `act` cannot emulate draft-release tag
   semantics or `gh` auth). The push itself runs the experimental workflow.
2. `gh workflow run release-experimental.yml -f dry_run=true` → inspect the
   `release-v*` run artifact and the printed Discord payload.
3. Set `DISCORD_RELEASE_WEBHOOK_URL` to a webhook in a private test server,
   dispatch with `dry_run=false` → real pre-release + Discord post. Push
   again → previous pre-release survives (prune keeps 10).
4. Stable: throwaway branch with `__version__ = "0.2.1"` and a `[0.2.1]`
   section, tag `v0.2.1`, push → release, `--latest`, Discord post; then
   `gh release delete v0.2.1 --cleanup-tag --yes`.
5. Negative cases: tag `v0.2.2` on a commit whose `__version__` is `0.2.1` →
   `verify` fails, nothing created; re-run a completed experimental run →
   `skip=true` notice.
6. Device checks: unpack a tarball on a Pi or Linux box; `scripts/start-kiosk.sh
   --mock` starts without building the UI; `openflight-server --version`,
   the Menu → System line, `openflight-cloud status`, and the session log's
   `session_start` record all show the same version and channel. On a plain
   `git clone` the same surfaces show `0.3.0+<sha> (source)`; copying the
   tree without `.git` still starts cleanly.

## Review decisions (all confirmed with the user)

| Area | Decision |
|------|----------|
| Architecture | Upstream `open-flight/openflight` is canonical; no repository guard (forks rehearse the same pipeline). `pytest.yml`/`ui-build.yml` become reusable and lose `push: main`; `release-experimental.yml` is main-branch CI. Experimental Discord posts fall back to `DISCORD_RELEASE_WEBHOOK_URL` when the experimental secret is unset. First stable is `0.3.0`. |
| Code quality | Experimental guard fails red with the fix in the message when `v<__version__>` exists. All release logic lives in tested `scripts/release/*.py`, called by the workflows. `release.json` parsing is strict (required keys, channel enum, `base_version` match) with `source` fallback. One local composite Discord action + one payload script shared by both workflows. |
| Tests | Keep a handful of text-level invariant tests on the workflow YAML. Unit tests plus one Playwright assertion for the version line. `build_artifact.py` tested against a real temporary git repo. Full live rehearsal on the fork including a throwaway `v0.2.1` stable tag and both negative cases. |
| Performance | Git SHA lookup is file-first, cached, 5 s timeout, only when `.git` and `git` exist. Tarball trimmed via `.gitattributes export-ignore`. Experimental releases gate on the full suites including e2e. Prune keeps the newest 10 experimental pre-releases. |
