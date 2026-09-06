"""Invariants of the release workflows that a careless edit could silently loosen."""

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
EXPERIMENTAL = (WORKFLOWS / "release-experimental.yml").read_text(encoding="utf-8")
STABLE = (WORKFLOWS / "release-stable.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("workflow", [EXPERIMENTAL, STABLE], ids=["experimental", "stable"])
def test_release_workflows_can_publish_but_never_fail_on_discord(workflow):
    assert "permissions:\n  contents: write" in workflow
    assert "concurrency:" in workflow
    assert "uses: ./.github/actions/announce-discord" in workflow
    announce = workflow.index("name: Announce on Discord")
    assert "continue-on-error: true" in workflow[announce : announce + 400]
    assert "--draft" in workflow and "--draft=false" in workflow


def test_each_channel_announces_through_its_own_webhook_only():
    assert "secrets.DISCORD_STABLE_WEBHOOK_URL" in STABLE
    assert "DISCORD_EXPERIMENTAL" not in STABLE
    assert "secrets.DISCORD_EXPERIMENTAL_WEBHOOK_URL" in EXPERIMENTAL
    assert "DISCORD_STABLE" not in EXPERIMENTAL
    webhook_line = next(line for line in EXPERIMENTAL.splitlines() if "webhook-url:" in line)
    assert "||" not in webhook_line


def test_release_workflows_gate_on_the_shared_test_suites():
    for workflow in (EXPERIMENTAL, STABLE):
        assert "uses: ./.github/workflows/pytest.yml" in workflow
        assert "uses: ./.github/workflows/ui-build.yml" in workflow
        assert "upload-dist: true" in workflow


def test_experimental_runs_on_main_and_refuses_an_already_released_base():
    assert "branches:\n      - main" in EXPERIMENTAL
    assert "cancel-in-progress: true" in EXPERIMENTAL
    assert "is already released; merge a bump first" in EXPERIMENTAL
    assert "git rev-list --count HEAD" in EXPERIMENTAL
    assert "--prerelease" in EXPERIMENTAL
    assert "--cleanup-tag" in EXPERIMENTAL


def test_stable_runs_only_on_plain_version_tags_and_verifies_first():
    assert '- "v[0-9]+.[0-9]+.[0-9]+"' in STABLE
    assert "cancel-in-progress: false" in STABLE
    assert "prepare_release.py check" in STABLE
    assert "needs: verify" in STABLE
    assert "--latest=" in STABLE


def test_shared_ci_workflows_are_reusable_and_leave_main_to_the_release_workflow():
    pytest_yml = (WORKFLOWS / "pytest.yml").read_text(encoding="utf-8")
    ui_yml = (WORKFLOWS / "ui-build.yml").read_text(encoding="utf-8")

    assert "workflow_call:" in pytest_yml and "workflow_call:" in ui_yml
    assert "branches:\n      - main" not in pytest_yml
    assert "branches:\n      - main" not in ui_yml
    assert "name: ui-dist" in ui_yml
