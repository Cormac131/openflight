# Release Process

OpenFlight ships on two release channels from a single `main` branch. Every
release is a GitHub Release with a downloadable artifact, and each device
knows which release it is running (Menu → System, `openflight-server
--version`, `openflight-cloud status`, and the `session_start` record of every
session log). This document is the maintainer's runbook; the design decisions
behind it are in [docs/plans/2026-09-04-release-channels-plan.md](plans/2026-09-04-release-channels-plan.md).

## Channels

| Channel | Tag | Produced by | GitHub Release |
|---------|-----|-------------|----------------|
| **stable** | `vX.Y.Z` | A maintainer pushes the tag | Marked *latest* when it is the highest stable version |
| **experimental** | `vX.Y.Z-dev.N` | Automatically on every push to `main` | Pre-release; only the newest 10 are kept |
| *source* | none | A plain `git clone` | none; the device reports `X.Y.Z+<commit>` |

`X.Y.Z` is `__version__` in `src/openflight/__init__.py`, the only place the
version lives (`pyproject.toml` reads it through hatchling). `N` is the commit
count on `main` (`git rev-list --count HEAD`), so it only ever grows.
Semver orders `v0.3.0-dev.42` before `v0.3.0`, which is why `__version__`
must be bumped immediately after a stable tag (see below); the experimental
workflow refuses to run while `v<__version__>` already exists.

## Cutting a stable release

1. Make sure `[Unreleased]` in `docs/CHANGELOG.md` says what shipped.
2. Roll the changelog and set the version, then refresh the lockfile:
   ```bash
   uv run python scripts/release/prepare_release.py release 0.3.0
   uv lock
   ```
   The script refuses to run when `[Unreleased]` is empty, when `[0.3.0]`
   already exists, or when the version does not increase.
3. Open a PR titled `chore(release): v0.3.0`, get it merged.
4. Tag the merge commit and push the tag:
   ```bash
   git fetch origin main
   git tag v0.3.0 origin/main
   git push origin v0.3.0
   ```
   `release-stable.yml` verifies the tag (`prepare_release.py check`), runs
   the full test suites, builds the artifact, publishes the release with the
   changelog section as notes, and announces it on Discord.
5. Start the next development version right away so experimental builds
   sort after the release:
   ```bash
   uv run python scripts/release/prepare_release.py next 0.4.0
   uv lock
   ```
   PR title: `chore: start 0.4.0 development`.

### Hotfixes

Branch from the stable tag, cherry-pick the fix, run
`prepare_release.py release X.Y.Z+1` on that branch, merge it to `main`
(resolving `__version__` in favour of `main` if it already moved on), and
tag the hotfix commit. The stable workflow warns when a tag is not on `main`
but still publishes. A hotfix cut after a newer minor never becomes *latest*.

### Recovery

- **Tag and version disagree** (`verify` fails): delete the tag (`git push
  origin :refs/tags/vX.Y.Z`), fix `__version__` or the changelog, retag.
- **A run was cancelled mid-publish**: releases are created as drafts and
  published in a second step, so a cancelled run leaves a draft and no tag.
  The next experimental run deletes stale drafts.
- **A release must be republished**: delete it deliberately (`gh release
  delete vX.Y.Z --cleanup-tag --yes`) and re-run `workflow_dispatch` with the
  tag, or push the tag again.

## Experimental releases

Every push to `main` runs the test suites and, when they pass, publishes
`v<__version__>-dev.<N>` as a pre-release with GitHub-generated notes since
the previous experimental release. Rapid merges cancel the in-flight run;
the newest push always wins. Older experimental releases beyond the newest
10 are deleted together with their tags.

Dry runs: **Actions → Experimental release → Run workflow** with *dry run*
checked builds the artifact (available as the `release-v…` run artifact) and
prints the Discord payload without publishing anything.

## What a release contains

`openflight-<tag>.tar.gz` unpacks to `openflight-<tag>/` holding:

- the tagged source tree, minus paths marked `export-ignore` in
  `.gitattributes` (models, CAD, session logs, design notes, PDFs; the
  IWR6843 firmware images stay in);
- `ui/dist/`, built by CI, so a Pi never runs `npm run build`;
- `release.json`, read by `openflight.release`:

  ```json
  {
    "format_version": 1,
    "version": "0.3.0-dev.42",
    "base_version": "0.3.0",
    "channel": "experimental",
    "tag": "v0.3.0-dev.42",
    "commit": "0123456789ab",
    "built_at": "2026-09-04T12:00:00+00:00",
    "repository": "open-flight/openflight"
  }
  ```

`openflight-<tag>.tar.gz.sha256` is in `sha256sum -c` format. A
`release.json` whose `base_version` does not match the code beside it is
ignored (the device reports the source channel instead), so a stale file
can never claim to be a release.

## Discord announcements

Both workflows post to a Discord webhook after the release is published.
The post never fails the workflow: a missing secret logs a warning, and a
failed post logs a warning.

1. In Discord: Server Settings → Integrations → Webhooks → New Webhook,
   pick the channel, **Copy Webhook URL**.
2. In GitHub: repository Settings → Secrets and variables → Actions → New
   repository secret:
   - `DISCORD_RELEASE_WEBHOOK_URL`: stable releases (and experimental ones
     until the next secret exists);
   - `DISCORD_EXPERIMENTAL_WEBHOOK_URL` (optional): experimental releases,
     so the per-merge posts can go to a quieter channel.
3. Rotate by regenerating the webhook in Discord and updating the secret.

Messages are capped at Discord's 2000 characters and never resolve
mentions; `scripts/release/discord_payload.py` builds them and is unit
tested.

## Rehearsing on a fork

The workflows carry no repository guard. On a fork, enable Actions, add the
Discord secret pointing at a private test server, and merge to the fork's
`main`; the experimental workflow runs for real against the fork's own
Releases. Delete rehearsal releases with `gh release delete <tag>
--cleanup-tag --yes` when done.

## What builds on this

- **Auto-update (stage 2)** reads `release.json` for the channel and the
  repository, polls `releases/latest` (stable) or the newest `-dev.`
  pre-release (experimental), downloads the tarball, verifies the checksum,
  runs `uv sync --locked`, and swaps the install.
- **Pi images (stage 3)** unpack a release into the image and preinstall its
  dependencies, one image per channel.
