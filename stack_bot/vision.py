"""Frame analysis for the Stack autoplayer.

Two things have to be found in each captured frame.

The sliding block is the only thing that moves between two consecutive
frames, so a frame difference isolates it without needing to know
anything about Stack's colour palette (which drifts through the rainbow
as you climb).

The top face of the tower is where the block has to land.  Stack's
background is a smooth vertical gradient, so a row of the image is
almost a single colour wherever the tower isn't.  Estimating that colour
from the left and right edges of each row and subtracting it leaves the
tower standing out.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

__all__ = ["Observation", "BlockTracker", "background_mask", "tower_top"]


@dataclass(frozen=True)
class Observation:
    """Where the sliding block was seen, and when."""

    t: float
    x: float
    y: float
    area: int


def background_mask(frame: np.ndarray, thresh: int = 18, edge_frac: float = 0.12) -> np.ndarray:
    """Mark the pixels of ``frame`` that are not background gradient.

    The background colour of a row is estimated from the leftmost and
    rightmost ``edge_frac`` of that row.  The tower never spans the full
    width of the capture region, so those columns are background nearly
    all of the time, and taking a median over both of them survives the
    sliding block covering one edge on its way in.
    """
    h, w = frame.shape[:2]
    k = max(2, int(w * edge_frac))
    edges = np.concatenate([frame[:, :k], frame[:, w - k :]], axis=1)
    bg = np.median(edges, axis=1, keepdims=True)
    delta = np.abs(frame.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
    mask = (delta > thresh).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def _tower_top_single(
    frame: np.ndarray,
    *,
    thresh: int,
    min_width_frac: float,
    band_frac: float,
    near: tuple[float, float] | None,
) -> tuple[float, float, int] | None:
    mask = background_mask(frame, thresh=thresh)
    h, w = mask.shape
    min_width = max(3, int(w * min_width_frac))
    band = max(3, int(h * band_frac))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return None

    best = None
    for i in range(1, n):
        top = stats[i, cv2.CC_STAT_TOP]
        height = stats[i, cv2.CC_STAT_HEIGHT]
        width = stats[i, cv2.CC_STAT_WIDTH]
        if width < min_width:
            continue
        # The tower runs off the bottom of the play area.  The sliding
        # block is always in mid-air, so this alone separates them.
        if top + height < h - max(2, int(0.01 * h)):
            continue
        r0 = int(top)
        r1 = min(h, r0 + band)
        ys, xs = np.nonzero(labels[r0:r1] == i)
        if xs.size < min_width:
            continue
        cx = float(xs.mean())
        cy = float(ys.mean()) + r0
        face = int(xs.max() - xs.min() + 1)
        if near is None:
            rank = -float(stats[i, cv2.CC_STAT_AREA])
        else:
            rank = (cx - near[0]) ** 2 + (cy - near[1]) ** 2
        if best is None or rank < best[0]:
            best = (rank, cx, cy, face)

    if best is None:
        return None
    return best[1], best[2], best[3]


def tower_top(
    frames: list[np.ndarray],
    *,
    thresh: int = 18,
    min_width_frac: float = 0.06,
    band_frac: float = 0.05,
    near: tuple[float, float] | None = None,
) -> tuple[float, float, int] | None:
    """Locate the centre of the tower's top face.

    Returns ``(x, y, width)`` in region coordinates, or ``None`` if no
    tower was found.  Several ``frames`` may be passed, in which case
    the per-frame answers are combined with a median, which throws out
    the odd frame caught mid-animation.

    ``near`` is the previous known top; when several structures qualify,
    the one closest to it wins.  Between one drop and the next the top
    moves only by however much got sliced off, so "closest to last time"
    is a reliable tie-break.
    """
    found = [
        r
        for r in (
            _tower_top_single(
                f,
                thresh=thresh,
                min_width_frac=min_width_frac,
                band_frac=band_frac,
                near=near,
            )
            for f in frames
        )
        if r is not None
    ]
    if not found:
        return None
    xs = sorted(r[0] for r in found)
    ys = sorted(r[1] for r in found)
    ws = sorted(r[2] for r in found)
    mid = len(found) // 2
    return xs[mid], ys[mid], ws[mid]


class BlockTracker:
    """Follow the sliding block by differencing consecutive frames.

    The difference of two frames lights up both the block's old and its
    new position, so the centroid of that blob sits midway between them
    -- half a frame behind the truth.  That lag is the same on every
    observation, so it shows up as a constant time offset and is
    absorbed by the lead that :class:`~predictor.Predictor` learns.
    """

    def __init__(
        self,
        *,
        diff_thresh: int = 14,
        min_area_frac: float = 0.0015,
        max_area_frac: float = 0.25,
        merge_frac: float = 0.40,
        merge_area_frac: float = 0.20,
    ) -> None:
        self.diff_thresh = diff_thresh
        self.min_area_frac = min_area_frac
        self.max_area_frac = max_area_frac
        self.merge_frac = merge_frac
        self.merge_area_frac = merge_area_frac
        self._prev: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None

    def update(self, frame: np.ndarray, t: float) -> Observation | None:
        """Return where the block is in ``frame``, or ``None``."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        prev, self._prev = self._prev, gray
        if prev is None or prev.shape != gray.shape:
            return None

        diff = cv2.absdiff(gray, prev)
        _, mask = cv2.threshold(diff, self.diff_thresh, 255, cv2.THRESH_BINARY)
        # Dilate rather than open.  A slow block shifts only two or
        # three pixels between frames, so the difference is a pair of
        # slivers barely wider than the erosion an open would apply --
        # opening deletes the block outright at low speed.  Dilating is
        # symmetric, so it costs nothing in centroid accuracy.
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.dilate(mask, kernel)

        total = gray.shape[0] * gray.shape[1]
        if mask.sum() / 255 > total * self.max_area_frac:
            # Nearly everything moved: the camera is scrolling after a
            # drop, or the palette just flipped.  Nothing to track.
            return None

        n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n <= 1:
            return None
        areas = stats[1:, cv2.CC_STAT_AREA]
        lead = int(np.argmax(areas))
        if int(areas[lead]) < total * self.min_area_frac:
            return None

        # Where the block overlaps itself between two frames the pixels
        # do not change, so the difference is not one blob but two: the
        # edge the block has uncovered and the edge it has newly
        # covered.  They are equally big, so taking only the largest
        # would report a point half a block away from the truth -- and
        # by a margin that grows as the block speeds up.  Gather every
        # comparable blob nearby instead; their combined centroid is the
        # midpoint of the block's travel over the frame.
        h, w = gray.shape
        lx, ly = centroids[lead + 1]
        near_x, near_y = w * self.merge_frac, h * self.merge_frac
        chosen = [
            i
            for i in range(len(areas))
            if areas[i] >= self.merge_area_frac * areas[lead]
            and abs(centroids[i + 1][0] - lx) <= near_x
            and abs(centroids[i + 1][1] - ly) <= near_y
        ]
        weight = float(sum(int(areas[i]) for i in chosen))
        cx = sum(int(areas[i]) * centroids[i + 1][0] for i in chosen) / weight
        cy = sum(int(areas[i]) * centroids[i + 1][1] for i in chosen) / weight
        return Observation(t=t, x=float(cx), y=float(cy), area=int(weight))
