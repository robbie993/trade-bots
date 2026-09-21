"""Deciding when to tap.

Stack is drawn in isometric projection, and the useful consequence is
that the vertical axis of the world is the vertical axis of the screen.
A block hovering above the tower is drawn higher up than the surface it
will land on, but at exactly the same horizontal position.  So the whole
problem is one number -- the gap between the block's screen x and the
tower top's screen x -- and a perfect drop is the moment that gap is
zero.  No camera model, no projection, no block geometry.

What is left is latency: the time between deciding to tap and the game
acting on it, covering screen capture, this process, the operating
system's input queue and the emulator.  That cannot be measured up
front, so the predictor guesses, watches where the block actually
landed, and corrects itself.  A few drops in, it is tapping early by
however long the round trip really takes.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .vision import Observation

__all__ = ["Motion", "Predictor"]


@dataclass(frozen=True)
class Motion:
    x: float
    y: float
    vx: float
    vy: float

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)


class Predictor:
    def __init__(
        self,
        *,
        window: int = 8,
        min_samples: int = 4,
        min_speed_x: float = 20.0,
        latency: float = 0.09,
        gain: float = 0.5,
        max_latency: float = 0.5,
    ) -> None:
        self.window = window
        self.min_samples = min_samples
        self.min_speed_x = min_speed_x
        self.latency = latency
        self.gain = gain
        self.max_latency = max_latency
        self._obs: deque[Observation] = deque(maxlen=window)
        self._vx_mag: float | None = None

    def reset(self) -> None:
        """Forget the current pass, but keep what speed the block runs at."""
        self._obs.clear()

    def add(self, obs: Observation) -> None:
        """Record an observation, dropping history across a bounce.

        The block reverses at each end of its travel.  Velocity fitted
        across a reversal is meaningless, so the history is cleared the
        moment the direction flips.
        """
        if len(self._obs) >= 2:
            a, b = self._obs[-2], self._obs[-1]
            if (b.x - a.x) * (obs.x - b.x) < 0:
                # This observation's frame pair straddles the turn, so
                # the two edges it sees belong to opposite directions
                # and the velocity it implies is meaningless.  Drop the
                # history and this sample with it; the next frame pair
                # lies wholly after the bounce.
                self._obs.clear()
                return
        self._obs.append(obs)

    def motion(self) -> Motion | None:
        """Least-squares position and velocity from recent observations.

        The fit is evaluated at the most recent observation's timestamp,
        so ``x`` is where the block was when that frame was captured.
        """
        if len(self._obs) < self.min_samples:
            return self._warm_motion()
        obs = list(self._obs)
        t0 = obs[-1].t
        ts = [o.t - t0 for o in obs]
        n = len(ts)
        mean_t = sum(ts) / n
        var_t = sum((t - mean_t) ** 2 for t in ts)
        if var_t <= 0:
            return None

        def fit(values: list[float]) -> tuple[float, float]:
            mean_v = sum(values) / n
            slope = sum((t - mean_t) * (v - mean_v) for t, v in zip(ts, values)) / var_t
            return mean_v - slope * mean_t, slope

        x, vx = fit([o.x for o in obs])
        y, vy = fit([o.y for o in obs])
        if abs(vx) < self.min_speed_x:
            return None
        self._vx_mag = abs(vx)
        return Motion(x=x, y=y, vx=vx, vy=vy)

    def _warm_motion(self) -> Motion | None:
        """A two-sample estimate that reuses the speed seen on earlier passes.

        Right after the block bounces there are not enough fresh samples
        to fit a velocity, and at high speed the alignment point can
        arrive before there are.  Stack changes the block's speed only
        gradually, so the magnitude carries over from the last full fit
        and the two newest samples supply position and direction.
        """
        if self._vx_mag is None or len(self._obs) < 2:
            return None
        a, b = self._obs[-2], self._obs[-1]
        span = b.t - a.t
        if span <= 0 or b.x == a.x:
            return None
        vx = math.copysign(self._vx_mag, b.x - a.x)
        if abs(vx) < self.min_speed_x:
            return None
        return Motion(x=b.x, y=b.y, vx=vx, vy=(b.y - a.y) / span)

    def time_to_drop(self, motion: Motion, target_x: float) -> float | None:
        """Seconds to wait before tapping, or ``None`` if not this pass.

        Negative means the moment has already gone by and the tap should
        go out immediately.
        """
        gap = motion.x - target_x
        if gap * motion.vx > 0:
            # Past the tower and still moving away: this pass is spent.
            return None
        return -gap / motion.vx - self.latency

    def observe_landing(self, vx: float, shift_x: float) -> float:
        """Correct the latency estimate from where the block landed.

        ``shift_x`` is how far the tower's top face moved horizontally
        after the drop.  Stack keeps the overlap between block and tower
        and slices off the rest, so the new top face is centred halfway
        between the old top and the block -- meaning the block itself
        missed by twice the shift.  A miss in the direction of travel
        says the tap went out too late.  Returns the miss, in pixels.
        """
        error = 2.0 * shift_x
        if vx == 0:
            return error
        self.latency += self.gain * (error / vx)
        self.latency = max(0.0, min(self.max_latency, self.latency))
        return error
