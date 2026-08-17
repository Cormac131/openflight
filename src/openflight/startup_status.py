"""Structured startup progress for the optional kiosk splash page."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class StartupComponent:
    """A configured component that should be visible during startup."""

    component_id: str
    label: str


def configured_startup_components(
    *,
    mock: bool,
    camera: bool,
    iwr6843: bool,
    inclinometer: bool,
    kld7: bool,
    kld7_horizontal: bool,
    battery: bool,
    simulators: bool,
) -> list[StartupComponent]:
    """Return only the components enabled for this OpenFlight process."""
    components = []
    if camera:
        components.append(StartupComponent("camera", "Camera"))
    if iwr6843:
        components.append(StartupComponent("ti", "TI radar"))
    if inclinometer:
        components.append(StartupComponent("inclinometer", "Inclinometer"))
    if kld7:
        components.append(StartupComponent("kld7_vertical", "K-LD7 launch radar"))
    if kld7_horizontal:
        components.append(StartupComponent("kld7_horizontal", "K-LD7 path radar"))
    components.append(
        StartupComponent("monitor", "Shot simulator")
        if mock
        else StartupComponent("ops", "OPS radar")
    )
    if battery:
        components.append(StartupComponent("battery", "Power monitor"))
    if simulators:
        components.append(StartupComponent("simulators", "Simulator connections"))
    return components


class StartupStatusReporter:
    """Publish versioned startup state to a JSON file using atomic replacement."""

    def __init__(self, path: Path | str | None, components: Iterable[StartupComponent]):
        self._path = Path(path) if path is not None else None
        self._components = {
            component.component_id: {
                "id": component.component_id,
                "label": component.label,
                "state": "waiting",
            }
            for component in components
        }
        self._overall = "starting"
        self._message = "Preparing OpenFlight"
        self._write()

    def start(self, component_id: str, message: str | None = None) -> None:
        """Mark a configured component as actively initializing."""
        self._set_component(component_id, "starting", message)

    def ready(self, component_id: str, message: str | None = None) -> None:
        """Mark a configured component as ready."""
        self._set_component(component_id, "ready", message)

    def skip(self, component_id: str, message: str | None = None) -> None:
        """Mark an optional configured component unavailable without failing startup."""
        self._set_component(component_id, "skipped", message)

    def error(self, component_id: str, message: str) -> None:
        """Record an initialization failure for a configured component."""
        self._overall = "error"
        self._set_component(component_id, "error", message)

    def finish(self, message: str = "OpenFlight is ready") -> None:
        """Mark the full startup sequence ready for browser handoff."""
        self._overall = "ready"
        self._message = message
        self._write()

    def _set_component(self, component_id: str, state: str, message: str | None) -> None:
        if component_id not in self._components:
            raise KeyError(f"Unknown startup component: {component_id}")
        self._components[component_id]["state"] = state
        if message is not None:
            self._message = message
        self._write()

    def _write(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "overall": self._overall,
            "message": self._message,
            "components": list(self._components.values()),
        }
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as temporary:
                json.dump(payload, temporary, separators=(",", ":"))
                temporary.write("\n")
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self._path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
