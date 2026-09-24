"""Enclosure orientation sensing for radar tilt compensation."""

from .lis3dh import LIS3DH, LIS3DHIdentityError
from .models import AccelerationSample, OrientationSnapshot, SnapshotSelection, StillnessState
from .placement import PlacementMonitor
from .service import InclinometerService

__all__ = [
    "AccelerationSample",
    "InclinometerService",
    "LIS3DH",
    "LIS3DHIdentityError",
    "OrientationSnapshot",
    "PlacementMonitor",
    "SnapshotSelection",
    "StillnessState",
]
