"""Hardware auto-detection and runtime flag generation.

Non-technical builds ship a prebuilt SD card image that cannot know which
optional hardware a given owner soldered in. This package probes the buses at
boot, records what it found, and turns that into the ``start-kiosk.sh`` flags
the server needs — so one image runs correctly on an OPS243-only build, an
OPS243 + IWR6843 build, or a legacy K-LD7 build without the owner editing
anything.
"""

from .detect import (
    DetectedDevice,
    DeviceKind,
    HardwareProfile,
    detect_geekworm_battery,
    detect_hardware,
    detect_inclinometer,
    detect_iwr6843_port,
    detect_kld7_ports,
    detect_ops243_port,
    detect_pi_camera,
)
from .flags import SiteConfig, profile_to_flags, render_env_file

__all__ = [
    "DeviceKind",
    "DetectedDevice",
    "HardwareProfile",
    "SiteConfig",
    "detect_geekworm_battery",
    "detect_hardware",
    "detect_inclinometer",
    "detect_iwr6843_port",
    "detect_kld7_ports",
    "detect_ops243_port",
    "detect_pi_camera",
    "profile_to_flags",
    "render_env_file",
]
