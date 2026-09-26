"""The shared IWR6843 test doubles must not drift from the real classes."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from iwr6843_fakes import FakeCaptureMonitor, FakeIWRRuntime

from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.iwr6843.runtime import IWR6843Runtime


def _public_members(cls) -> list[str]:
    return [name for name in vars(cls) if not name.startswith("_")]


@pytest.mark.parametrize("name", _public_members(FakeIWRRuntime))
def test_fake_runtime_member_exists_on_the_real_runtime(name):
    assert hasattr(IWR6843Runtime, name) or name in IWR6843Runtime.__dataclass_fields__


@pytest.mark.parametrize(
    "name",
    [
        name
        for name in _public_members(FakeIWRRuntime)
        if inspect.isfunction(getattr(FakeIWRRuntime, name))
    ],
)
def test_fake_runtime_methods_take_the_real_parameters(name):
    fake = inspect.signature(getattr(FakeIWRRuntime, name))
    real = inspect.signature(getattr(IWR6843Runtime, name))

    assert [(p.name, p.kind) for p in fake.parameters.values()] == [
        (p.name, p.kind) for p in real.parameters.values()
    ]


def test_fake_runtime_covers_every_runtime_method_the_server_calls():
    server_calls = {
        "process_shot",
        "plan_late_window",
        "measure_late_window",
        "self_trigger_enabled",
        "stop",
    }

    assert server_calls <= set(_public_members(FakeIWRRuntime))


# Every capture-monitor member the server reaches for, either directly on
# ``capture_monitor`` or through ``IWR6843Runtime``. ``self_trigger`` backs the
# ``watch_self_trigger`` property, so the double needs the attribute itself and
# not just the derived flag.
SERVER_CAPTURE_MONITOR_MEMBERS = frozenset(
    {
        "add_trigger_observer",
        "arm",
        "onboard_tracking",
        "port",
        "radar",
        "self_trigger",
        "slice_planner",
        "start",
        "stop",
        "watch_self_trigger",
    }
)


def _real_capture_monitor_members() -> set[str]:
    """Class members plus constructor keywords of the real capture monitor.

    Attributes such as ``self_trigger`` only exist on instances, so a plain
    ``hasattr`` on the class misses them.
    """
    members = {name for name in dir(IWR6843CaptureMonitor) if not name.startswith("_")}
    members |= set(inspect.signature(IWR6843CaptureMonitor.__init__).parameters)
    return members


@pytest.mark.parametrize("name", sorted(SERVER_CAPTURE_MONITOR_MEMBERS))
def test_fake_capture_monitor_has_every_member_the_server_touches(name):
    assert (
        hasattr(FakeCaptureMonitor, name)
        or name in inspect.signature(FakeCaptureMonitor.__init__).parameters
    )


@pytest.mark.parametrize("name", sorted(SERVER_CAPTURE_MONITOR_MEMBERS))
def test_server_capture_monitor_members_are_real(name):
    """Guard the list above: a renamed production member must fail here too."""
    assert name in _real_capture_monitor_members()


@pytest.mark.parametrize(
    "name",
    sorted(
        name
        for name in SERVER_CAPTURE_MONITOR_MEMBERS
        if inspect.isfunction(getattr(IWR6843CaptureMonitor, name, None))
    ),
)
def test_fake_capture_monitor_methods_take_the_real_parameters(name):
    fake = inspect.signature(getattr(FakeCaptureMonitor, name))
    real = inspect.signature(getattr(IWR6843CaptureMonitor, name))

    assert [(p.name, p.kind) for p in fake.parameters.values()] == [
        (p.name, p.kind) for p in real.parameters.values()
    ]


def test_fake_capture_monitor_reports_no_self_trigger_by_default():
    """``init_iwr6843`` builds the monitor without a self-trigger config."""
    monitor = FakeCaptureMonitor()

    assert monitor.self_trigger is None
    assert monitor.watch_self_trigger is False


def test_fake_capture_monitor_watches_a_configured_self_trigger():
    monitor = FakeCaptureMonitor(self_trigger=SimpleNamespace(command="triggerCfg 14 1000.0 2"))

    assert monitor.watch_self_trigger is True
