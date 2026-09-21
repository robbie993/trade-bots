"""Run the tracker and predictor against a synthetic Stack-like scene.

Nothing here touches the screen, so it runs anywhere, and it checks the
part of the bot that is actually hard: seeing the block, fitting its
motion, and converging on a tap that lands in the middle.  The fake
scene is deliberately unkind.  The tap goes out later than the
predictor's configured latency, so the correction loop has to find the
difference.  The block hovers well above the surface it lands on, to
confirm that hover really is irrelevant.  The travel axis alternates
between drops the way Stack's does, and the block speeds up as the
tower grows.
"""

from __future__ import annotations

import math

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("selftest needs opencv: pip install opencv-python") from exc

from .predictor import Predictor
from .vision import BlockTracker, tower_top

W, H = 400, 600
TOWER_TOP_Y = 380
TOWER_CX = 200.0
TOWER_HALF_W = 60
BLOCK_W, BLOCK_H = 120, 44
HOVER = 46.0                       # block centre sits this far above the surface
SLOPE = 0.5                        # 2:1 isometric
AMPLITUDE = 150.0                  # half the travel, in screen x
BASE_SPEED = 230.0                 # screen x pixels per second, first drop
SPEEDUP = 1.035                    # per drop, as the tower grows
FPS = 60.0
TRUE_LATENCY = 0.115               # what a tap really costs, end to end


def background() -> np.ndarray:
    g = np.linspace(40, 130, H, dtype=np.float32).reshape(H, 1, 1)
    tint = np.array([1.35, 0.85, 0.65], dtype=np.float32).reshape(1, 1, 3)
    col = np.clip(g * tint, 0, 255).astype(np.uint8)
    return np.repeat(col, W, axis=1)


def phase_rate(speed: float, dt: float) -> float:
    """Phase per frame that moves the block at ``speed`` px/s in x.

    The triangle wave swings through ``2 * AMPLITUDE`` of travel per
    unit of phase, so the rate is halved.
    """
    return speed / (2.0 * AMPLITUDE) * dt


def block_centre(phase: float, axis_sign: int) -> tuple[float, float]:
    """Triangle-wave travel through the alignment point."""
    tri = 2.0 * abs(((phase + 0.5) % 2.0) - 1.0) - 1.0
    dx = AMPLITUDE * tri
    return TOWER_CX + dx, (TOWER_TOP_Y - HOVER) + axis_sign * SLOPE * dx


def render(block: tuple[float, float] | None, tower_cx: float = TOWER_CX) -> np.ndarray:
    frame = background()
    x0 = int(tower_cx - TOWER_HALF_W)
    cv2.rectangle(frame, (x0, TOWER_TOP_Y), (x0 + 2 * TOWER_HALF_W, H - 1), (70, 190, 210), -1)
    if block is not None:
        bx, by = block
        cv2.rectangle(
            frame,
            (int(bx - BLOCK_W / 2), int(by - BLOCK_H / 2)),
            (int(bx + BLOCK_W / 2), int(by + BLOCK_H / 2)),
            (200, 120, 240),
            -1,
        )
    return frame


def check_tower_top() -> float:
    """The block is in mid-air; the tower runs off the bottom."""
    frames = [render(block_centre(0.13 * i, 1)) for i in range(9)]
    found = tower_top(frames)
    assert found is not None, "tower_top found nothing"
    x, y, width = found
    assert abs(x - TOWER_CX) < 4.0, f"tower centre off by {x - TOWER_CX:+.1f}px"
    assert y >= TOWER_TOP_Y, f"tower top row {y:.1f} is above the tower"
    assert abs(width - 2 * TOWER_HALF_W) < 8, f"top face width {width}, expected ~{2*TOWER_HALF_W}"
    print(f"  tower top  x={x:7.2f} (true {TOWER_CX:.2f})  y={y:7.2f}  face width={width}")
    return x


def run_drops(
    target_x: float,
    *,
    drops: int = 16,
    fps: float = FPS,
    base_speed: float = BASE_SPEED,
    true_latency: float = TRUE_LATENCY,
) -> tuple[list[float], float]:
    """Play ``drops`` blocks against the fake scene.

    Returns the landing error of each drop in pixels, and the latency
    the predictor ended up believing in.
    """
    tracker = BlockTracker()
    predictor = Predictor(latency=0.09)

    errors: list[float] = []
    dt = 1.0 / fps
    phase = 0.0
    t = 0.0

    for drop in range(drops):
        speed = base_speed * (SPEEDUP ** drop)
        axis_sign = 1 if drop % 2 == 0 else -1
        rate = phase_rate(speed, dt)
        tracker.reset()
        predictor.reset()
        fired = False

        for _ in range(int(8.0 * fps)):
            phase += rate
            t += dt
            obs = tracker.update(render(block_centre(phase, axis_sign)), t)
            if obs is None:
                continue
            predictor.add(obs)
            motion = predictor.motion()
            if motion is None:
                continue
            wait = predictor.time_to_drop(motion, target_x)
            # Commit only while the moment is still ahead, then wait for
            # it exactly.  The block slides back and forth until it is
            # tapped, so a pass that is already spent costs nothing to
            # skip and the next one comes round in about a second.
            if wait is None or wait < 0.0 or wait > 2 * dt:
                continue

            # The tap lands after the real pipeline latency, not the
            # predictor's guess at it.
            land_phase = phase + (wait + true_latency) / dt * rate
            bx, _ = block_centre(land_phase, axis_sign)
            error = bx - target_x
            errors.append(error)
            predictor.observe_landing(motion.vx, error / 2.0)
            fired = True
            break

        if not fired:
            raise AssertionError(f"predictor never fired on drop {drop + 1}")
        phase += 0.37   # start the next pass somewhere else in the travel

    return errors, predictor.latency


def sweep() -> None:
    """Where does it hold up, and where does it fall apart?"""
    print("  settled error (px, worst of last six) by capture rate and block speed:")
    speeds = (150.0, 250.0, 400.0, 650.0, 900.0)
    print("      fps |" + "".join(f"{s:>9.0f}" for s in speeds))
    print("    ------+" + "-" * (9 * len(speeds)))
    for fps in (30.0, 60.0, 120.0):
        cells = []
        for sp in speeds:
            try:
                errs, _ = run_drops(TOWER_CX, drops=18, fps=fps, base_speed=sp)
                worst = max(abs(e) for e in errs[-6:])
                cells.append(f"{worst:>9.2f}")
            except AssertionError:
                cells.append(f"{'no fire':>9}")
        print(f"    {fps:5.0f} |" + "".join(cells))


def check_loop() -> list[float]:
    """Drive the real bot loop against a scene on the real clock.

    :func:`run_drops` steps a simulated clock, so it says nothing about
    whether the loop in :mod:`stack_bot.bot` is wired up, nor whether
    the tap lands where it was scheduled once screen capture and a
    sleep are in the way.  This runs the actual ``measure``/``track``
    cycle, with the screen replaced by a rendered scene that follows
    ``perf_counter`` and a tap that records where the block was.
    """
    import time

    from .bot import Bot, Region, build_parser

    speed = 300.0
    origin = TOWER_CX
    state = {"tower_cx": TOWER_CX, "errors": []}
    t0 = time.perf_counter()

    def block_now() -> tuple[float, float]:
        elapsed = time.perf_counter() - t0
        phase = speed * elapsed / (2.0 * AMPLITUDE)
        tri = 2.0 * abs(((phase + 0.5) % 2.0) - 1.0) - 1.0
        dx = AMPLITUDE * tri
        return origin + dx, (TOWER_TOP_Y - HOVER) + SLOPE * dx

    args = build_parser().parse_args(
        ["--settle", "0.06", "--measure-gap", "0.015", "--track-timeout", "6", "--max-fps", "120"]
    )
    bot = Bot(Region(0, 0, W, H), args)
    bot.grab = lambda _sct: render(block_now(), state["tower_cx"])

    def tap() -> None:
        bx, _ = block_now()
        error = bx - state["tower_cx"]
        state["errors"].append(error)
        # Stack keeps the overlap and slices the rest, so the surviving
        # top face is centred halfway between tower and block.
        state["tower_cx"] += error / 2.0

    bot.click = tap

    for _ in range(8):
        assert bot.measure(None), "measure() lost the tower"
        assert bot.track(None), "track() never tapped"
        time.sleep(args.settle)
    assert bot.measure(None), "measure() lost the tower at the end"

    errors = state["errors"]
    print("  live-clock loop, landing error by drop (px):")
    print("   " + "  ".join(f"{e:+.1f}" for e in errors))
    print(f"  learned latency {bot.predictor.latency*1000:5.1f}ms (the fake tap is instant, "
          f"so this is capture and scheduling overhead)")
    settled = max(abs(e) for e in errors[-4:])
    print(f"  last four within {settled:.2f}px")
    assert bot.drops == len(errors) == 8, f"expected 8 drops, got {bot.drops}/{len(errors)}"
    assert settled < 6.0, f"live loop did not settle: {settled:.2f}px"
    return errors


def main() -> int:
    print("stack_bot selftest")
    print(f"  scene {W}x{H}, block {BASE_SPEED:.0f}px/s rising, true latency {TRUE_LATENCY*1000:.0f}ms")

    tx = check_tower_top()
    errors, learned = run_drops(tx)
    print(f"  learned latency {learned*1000:5.1f}ms (true {TRUE_LATENCY*1000:.0f}ms, "
          f"plus half a frame of tracking lag)")
    print("  landing error by drop (px):")
    print("   " + "  ".join(f"{e:+.1f}" for e in errors))

    worst = max(abs(e) for e in errors[-6:])
    print(f"  first drop off by {abs(errors[0]):.1f}px, last six within {worst:.2f}px")
    assert worst < 1.5, f"did not converge: settled error {worst:.2f}px"

    sweep()
    check_loop()
    print("  OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
