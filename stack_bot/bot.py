"""The loop: watch a region of the screen, tap when the block lines up.

Run ``python -m stack_bot`` with the game visible in a window, pick the
play area with the mouse, and leave it alone.  Escape stops it.

The loop is a three-state cycle per block.  MEASURE finds the tower's
top face and takes its horizontal centre as the target.  TRACK follows
the sliding block until the predictor says the alignment moment is
close, then sleeps to that moment exactly and taps.  SETTLE waits out
the fall and the camera scroll, and hands back to MEASURE, which sees
where the block really landed and tells the predictor how far off the
tap was.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .predictor import Predictor
from .vision import BlockTracker, tower_top

CONFIG_PATH = Path.home() / ".stack_bot.json"


@dataclass
class Region:
    left: int
    top: int
    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    @classmethod
    def parse(cls, text: str) -> "Region":
        parts = [int(p) for p in text.replace(" ", "").split(",")]
        if len(parts) != 4:
            raise ValueError("region must be left,top,width,height")
        return cls(*parts)


def sleep_until(deadline: float) -> None:
    """Sleep to ``deadline`` (a ``perf_counter`` value) accurately.

    ``time.sleep`` is only good to a millisecond or two, which at a few
    hundred pixels a second is a few pixels of error, so the last stretch
    is spun out rather than slept.
    """
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.003:
            time.sleep(remaining - 0.002)


def select_region() -> Region:
    """Drag a box around the play area."""
    try:
        import tkinter as tk
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "tkinter is not available, so pass the play area explicitly:\n"
            "  python -m stack_bot --region LEFT,TOP,WIDTH,HEIGHT"
        ) from exc

    print("Drag a box around the play area (Escape to cancel).")
    box: dict[str, int] = {}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    try:
        root.attributes("-alpha", 0.25)
    except tk.TclError:
        pass
    root.configure(bg="black")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, cursor="crosshair", bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    start: dict[str, int] = {}
    rect = {"id": None}

    def on_press(event: "tk.Event") -> None:
        start["x"], start["y"] = event.x_root, event.y_root
        if rect["id"] is not None:
            canvas.delete(rect["id"])
        rect["id"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="white", width=2)

    def on_drag(event: "tk.Event") -> None:
        if rect["id"] is not None:
            x0 = start["x"] - root.winfo_rootx()
            y0 = start["y"] - root.winfo_rooty()
            canvas.coords(rect["id"], x0, y0, event.x, event.y)

    def on_release(event: "tk.Event") -> None:
        box["left"] = min(start["x"], event.x_root)
        box["top"] = min(start["y"], event.y_root)
        box["width"] = abs(event.x_root - start["x"])
        box["height"] = abs(event.y_root - start["y"])
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()

    if not box or box["width"] < 50 or box["height"] < 50:
        raise SystemExit("No usable region selected.")
    return Region(box["left"], box["top"], box["width"], box["height"])


class Bot:
    def __init__(self, region: Region, args: argparse.Namespace) -> None:
        self.region = region
        self.args = args
        self.tracker = BlockTracker()
        self.predictor = Predictor(latency=args.latency)
        self.target_x: float | None = None
        self.last_top: tuple[float, float] | None = None
        self.pending_vx: float | None = None
        self.mouse = None
        self.button = None
        self.drops = 0
        self.stop = False

    # -- screen ---------------------------------------------------------

    def grab(self, sct) -> np.ndarray:
        shot = sct.grab(self.region.as_dict())
        return np.asarray(shot)[:, :, :3]

    def click(self) -> None:
        if self.args.dry_run:
            return
        self.mouse.click(self.button)

    # -- states ---------------------------------------------------------

    def measure(self, sct) -> bool:
        """Find the tower's top face and make it the target.

        Called once before the first block, and again after every drop,
        which is what closes the loop: the difference between the new
        top and the old one says how far the last tap was off.
        """
        frames = []
        for _ in range(self.args.measure_frames):
            frames.append(self.grab(sct))
            time.sleep(self.args.measure_gap)
        found = tower_top(frames, thresh=self.args.tower_thresh, near=self.last_top)
        if found is None:
            return False
        x, y, width = found
        previous, self.last_top = self.last_top, (x, y)
        self.target_x = x
        if previous is not None and self.pending_vx is not None:
            error = self.predictor.observe_landing(self.pending_vx, x - previous[0])
            if self.args.verbose:
                print(
                    f"  drop {self.drops:4d}  missed by {error:+7.2f}px  "
                    f"face {width:4d}px  latency now {self.predictor.latency*1000:6.1f}ms"
                )
            self.pending_vx = None
        return True

    def track(self, sct) -> bool:
        """Follow the block and tap.  False if the block was never found."""
        assert self.target_x is not None
        self.tracker.reset()
        self.predictor.reset()
        deadline = time.perf_counter() + self.args.track_timeout
        frame_budget = 1.0 / self.args.max_fps if self.args.max_fps else 0.0

        while time.perf_counter() < deadline and not self.stop:
            started = time.perf_counter()
            observation = self.tracker.update(self.grab(sct), started)
            if observation is not None:
                self.predictor.add(observation)
                motion = self.predictor.motion()
                if motion is not None:
                    wait = self.predictor.time_to_drop(motion, self.target_x)
                    # Commit only while the moment is still ahead.  The
                    # block slides back and forth until it is tapped, so
                    # a pass that is already spent costs nothing to skip.
                    if wait is not None and 0.0 <= wait <= self.args.commit_horizon:
                        sleep_until(started + wait)
                        self.click()
                        self.pending_vx = motion.vx
                        self.drops += 1
                        return True
            if frame_budget:
                sleep_until(started + frame_budget)
        return False

    # -- driver ---------------------------------------------------------

    def run(self) -> int:
        try:
            import mss
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("pip install mss") from exc
        from pynput import keyboard
        from pynput.mouse import Button, Controller

        self.mouse = Controller()
        self.button = Button.left

        listener = keyboard.Listener(on_press=self._on_key)
        listener.start()

        click_x = self.region.left + self.region.width // 2
        click_y = self.region.top + self.region.height // 2
        print(f"Play area {self.region.as_dict()}")
        print(f"Tapping at ({click_x}, {click_y}). Escape stops.")
        if self.args.dry_run:
            print("Dry run: watching and reporting, never clicking.")
        for n in range(self.args.countdown, 0, -1):
            print(f"  starting in {n}...", end="\r", flush=True)
            time.sleep(1.0)
        print(" " * 30, end="\r")

        if not self.args.dry_run:
            self.mouse.position = (click_x, click_y)
            time.sleep(0.2)

        misses = 0
        with mss.mss() as sct:
            while not self.stop:
                if self.args.max_drops and self.drops >= self.args.max_drops:
                    print(f"Reached {self.args.max_drops} drops.")
                    break
                if not self.measure(sct):
                    misses += 1
                    if misses >= 5:
                        print("Lost the tower -- game over, or the play area moved.")
                        break
                    continue
                misses = 0
                if not self.track(sct):
                    print("Lost the block -- game over, or it stopped moving.")
                    break
                time.sleep(self.args.settle)

        listener.stop()
        print(f"Stopped after {self.drops} drops.")
        return 0

    def _on_key(self, key) -> None:
        from pynput import keyboard

        if key == keyboard.Key.esc:
            self.stop = True


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        print(f"Could not save {CONFIG_PATH}: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stack_bot",
        description="Play Stack (Ketchapp) by watching the screen and tapping on time.",
    )
    p.add_argument("--region", help="play area as LEFT,TOP,WIDTH,HEIGHT; omit to pick it with the mouse")
    p.add_argument("--reselect", action="store_true", help="pick the play area again, ignoring the saved one")
    p.add_argument("--latency", type=float, default=0.09,
                   help="first guess at tap latency in seconds; it corrects itself (default 0.09)")
    p.add_argument("--max-fps", type=float, default=120.0, help="cap on capture rate (default 120)")
    p.add_argument("--commit-horizon", type=float, default=0.02,
                   help="commit to a tap when the moment is this close, in seconds (default 0.02)")
    p.add_argument("--settle", type=float, default=0.35,
                   help="pause after a tap for the fall and the camera, in seconds (default 0.35)")
    p.add_argument("--measure-frames", type=int, default=3, help="frames to average when finding the tower")
    p.add_argument("--measure-gap", type=float, default=0.02, help="seconds between those frames")
    p.add_argument("--tower-thresh", type=int, default=18,
                   help="how far from the background a pixel must be to count as tower (default 18)")
    p.add_argument("--track-timeout", type=float, default=8.0, help="give up on a block after this long")
    p.add_argument("--countdown", type=int, default=3, help="seconds before starting, to focus the game window")
    p.add_argument("--max-drops", type=int, default=0, help="stop after this many blocks (0 = no limit)")
    p.add_argument("--dry-run", action="store_true", help="watch and report, but never click")
    p.add_argument("--verbose", action="store_true", help="report every drop")
    p.add_argument("--selftest", action="store_true", help="check the tracker and predictor, touching nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.selftest:
        from .selftest import main as selftest_main

        return selftest_main()

    config = load_config()
    if args.region:
        region = Region.parse(args.region)
    elif config.get("region") and not args.reselect:
        region = Region(**config["region"])
        print("Using the saved play area; --reselect to change it.")
    else:
        region = select_region()
        config["region"] = region.as_dict()
        save_config(config)
        print(f"Saved the play area to {CONFIG_PATH}")

    return Bot(region, args).run()


if __name__ == "__main__":
    raise SystemExit(main())
