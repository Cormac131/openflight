"""Synthetic down-the-line camera frames for address-trigger tests.

Frames are small monochrome images: a textured mat, an optional bright ball
disc, and an optional dark clubhead rectangle. Noise is seeded so scenarios are
reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

import numpy as np

from openflight.camera.address_trigger import (
    AddressEvent,
    BallAddressStateMachine,
    BallCandidate,
)

WIDTH = 160
HEIGHT = 120
BALL_X = 80
BALL_Y = 70
BALL_RADIUS = 6
FRAME_NS = 3_333_333  # 300 fps


@dataclass(frozen=True)
class Scene:
    """One frame's contents."""

    ball: Optional[tuple[float, float]] = (BALL_X, BALL_Y)
    ball_radius: float = BALL_RADIUS
    club: Optional[tuple[int, int, int, int]] = None  # x0, y0, x1, y1
    gain: float = 1.0
    debris: bool = False


BALL = Scene()
EMPTY = Scene(ball=None)
# Club soled beside (heel side of) the ball: touches the surround, not the ROI.
ADDRESS = Scene(club=(BALL_X - 26, BALL_Y - 12, BALL_X - 11, BALL_Y + 13))
# Clubhead directly between camera and ball: covers the whole ROI.
OCCLUDED = Scene(club=(BALL_X - 14, BALL_Y - 14, BALL_X + 15, BALL_Y + 15))
OCCLUDED_EMPTY = Scene(ball=None, club=OCCLUDED.club)


class FrameFactory:
    """Render scenes onto a fixed textured mat."""

    def __init__(self, seed: int = 7, noise: float = 2.0):
        rng = np.random.default_rng(seed)
        self._rng = np.random.default_rng(seed + 1)
        self._noise = noise
        # Coarse mat texture: mid-grey with gentle blotches.
        base = rng.normal(70.0, 4.0, size=(HEIGHT // 4, WIDTH // 4))
        self._mat = np.kron(base, np.ones((4, 4)))[:HEIGHT, :WIDTH].astype(np.float32)
        self._yy, self._xx = np.mgrid[0:HEIGHT, 0:WIDTH]

    def render(self, scene: Scene) -> np.ndarray:
        """Render one frame as uint8."""
        img = self._mat.copy()
        if scene.debris:
            img[BALL_Y - 5 : BALL_Y + 6, BALL_X - 9 : BALL_X + 10] = 25.0
        if scene.ball is not None:
            bx, by = scene.ball
            dist = np.hypot(self._xx - bx, self._yy - by)
            # Shaded ball: bright core fading to the rim (gives NCC structure).
            shade = 220.0 - 60.0 * (dist / max(scene.ball_radius, 1e-6)) ** 2
            img = np.where(dist <= scene.ball_radius, shade, img)
        if scene.club is not None:
            x0, y0, x1, y1 = scene.club
            img[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = 30.0
            img[max(y0, 0) : max(y0 + 2, 0), max(x0, 0) : max(x1, 0)] = 150.0  # topline glint
        if self._noise:
            img = img + self._rng.normal(0.0, self._noise, size=img.shape)
        img = img * scene.gain
        return np.clip(img, 0, 255).astype(np.uint8)


def candidate_for(scene: Scene) -> Optional[BallCandidate]:
    """What an ideal acquisition detector would report for ``scene``."""
    if scene.ball is None or scene.club == OCCLUDED.club:
        return None
    return BallCandidate(float(scene.ball[0]), float(scene.ball[1]), float(scene.ball_radius))


def expand(steps: Iterable[tuple[Scene, int]]) -> Iterator[Scene]:
    """Expand ``[(scene, count), ...]`` into a flat scene sequence."""
    for scene, count in steps:
        for _ in range(count):
            yield scene


class ScenarioRunner:
    """Drive a state machine through scenes at a fixed frame rate."""

    def __init__(
        self,
        machine: BallAddressStateMachine,
        factory: Optional[FrameFactory] = None,
        start_ns: int = 1_000_000_000,
    ):
        self.machine = machine
        self.factory = factory or FrameFactory()
        self.now_ns = start_ns
        self.events: list[AddressEvent] = []

    def lock(self, scene: Scene = BALL, *, empty_first: int = 2) -> None:
        """Acquire ``scene``'s ball, optionally seeding bare-mat history first."""
        for _ in range(empty_first):
            self.events += self.machine.acquire(
                self.factory.render(EMPTY), self.now_ns, None, scene.gain
            )
        for _ in range(self.machine.config.stable_acquisitions):
            self.events += self.machine.acquire(
                self.factory.render(scene), self.now_ns, candidate_for(scene), scene.gain
            )

    def run(self, steps: Iterable[tuple[Scene, int]], *, frame_ns: int = FRAME_NS) -> None:
        """Feed each expanded scene as one frame."""
        for scene in expand(steps):
            self.now_ns += frame_ns
            self.events += self.machine.feed(self.factory.render(scene), self.now_ns, scene.gain)

    def gap(self, duration_ns: int) -> None:
        """Advance the clock without frames (dropped frames / stall)."""
        self.now_ns += duration_ns

    def kinds(self) -> list[str]:
        """Event kinds seen so far."""
        return [event.kind for event in self.events]

    def triggers(self) -> list[AddressEvent]:
        """Trigger events seen so far."""
        return [event for event in self.events if event.kind == "trigger"]
