"""The shared IWR6843 test doubles must not drift from the real classes."""

from __future__ import annotations

import inspect

import pytest
from iwr6843_fakes import FakeIWRRuntime

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
