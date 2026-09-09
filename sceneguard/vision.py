"""
Vision lane: scene-boundary map + a deliberately honest nudity *proxy*.

IMPORTANT / read before you build a company on this file:

  The classifier here is a classical-features PROXY (skin-tone mass, edge
  density, shot type, colour dispersion). It is there to prove the PIPELINE is
  right - boundary map in, candidate segments out, resync and merge, manifest
  written. It is NOT accurate enough to ship as the product's sole decision.

Real accuracy in this category comes from either (a) a fine-tuned NSFW
classifier over shots, run offline on the user's OWN files only, or (b)
licensed / community timestamps. On streaming content you cannot run anything
over frames anyway, because the frames are Widevine-encrypted and decrypting
them is the one thing that turns this from a consumer tool into a lawsuit
(17 U.S.C. 1201 in the US; s.65A of the Copyright Act, 1957 in India).

So: this module is for local, DRM-free files. That is also exactly where it is
useful - home video, purchased MP4s, and any Indian short film you have rights
to.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class FrameStats:
    t: float
    boundary: bool
    skin: float           # fraction of frame pixels in skin range
    edge: float           # Canny edge density -> clothing / background clutter
    disp: float           # std-dev of chroma over skin pixels -> low = bare skin field
    brightness: float
    closeup: bool         # large contiguous central blob => faceless torso close-up

    @property
    def score(self) -> float:
        """0..1 proxy score for 'undressed-person frame'. Tuned to be conservative."""
        if self.skin < 0.16:
            return 0.0
        skin_term = min(1.0, (self.skin - 0.12) / 0.45)
        edge_term = max(0.0, 1.0 - self.edge / 0.055)          # clothed = busy edges
        disp_term = max(0.0, 1.0 - self.disp / 42.0)          # bare skin = flat chroma
        closeup_term = 1.0 if self.closeup else 0.55
        s = skin_term * (0.42 * edge_term + 0.30 * disp_term + 0.28) * closeup_term
        return round(float(min(1.0, max(0.0, s))), 3)


def _frame_stats(t: float, frame: np.ndarray, prev_gray: np.ndarray | None) -> tuple[FrameStats, np.ndarray]:
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (21, 21), 0)

    # skin mask in YCrCb - the classical, well-understood test
    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, (0, 135, 85), (255, 180, 135))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    skin = float(mask.sum()) / 255.0 / float(h * w)

    edges = cv2.Canny(blur, 40, 120)
    edge = float((edges > 0).sum()) / float(h * w)

    ys, xs = np.nonzero(mask)
    if len(ys) > 400:
        sub = frame[ys, xs].astype(np.float32)
        disp = float(np.mean(np.std(sub, axis=0)))
        # close-up heuristic: one dominant blob occupying the middle band
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        biggest = max(cv2.contourArea(c) for c in cnts) if cnts else 0.0
        closeup = biggest > 0.28 * h * w and (xs.min() < 0.25 * w and xs.max() > 0.75 * w)
    else:
        disp, closeup = 99.0, False

    boundary = False
    if prev_gray is not None:
        d = float(np.mean(cv2.absdiff(prev_gray, blur)))
        boundary = d > 18.0

    st = FrameStats(
        t=round(t, 3),
        boundary=boundary,
        skin=round(skin, 4),
        edge=round(edge, 5),
        disp=round(disp, 2),
        brightness=round(float(gray.mean()) / 255.0, 3),
        closeup=bool(closeup),
    )
    return st, blur


def sample_video(path: str, *, fps: float = 2.0, max_frames: int = 4000):
    """Yield (stats_list, fps_native, frame_count, duration)."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    native = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(native / fps)))
    stats: list[FrameStats] = []
    blurs: list[np.ndarray] = []
    prev = None
    i = -1
    while True:
        ok = cap.grab()
        if not ok:
            break
        i += 1
        if i % step:
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        st, prev = _frame_stats(i / native, frame, prev)
        stats.append(st)
        blurs.append(prev)
        if len(stats) >= max_frames:
            break
    cap.release()
    # stash the smoothed grayscale series so boundary detection can be adaptive
    stats_series = _adaptive_boundaries(stats, blurs)
    duration = (total / native) if total and native else (stats[-1].t if stats else 0.0)
    return stats_series, native, len(stats_series), duration


def _adaptive_boundaries(stats: list[FrameStats], blurs: list[np.ndarray]) -> list[FrameStats]:
    """Mark shot changes with a *relative* threshold.

    A fixed mean-abs-diff cutoff (the naive way) fails the moment you change
    genre: an action film's car chase sits above it, a chamber drama's dialogue
    scene sits below it, and you get either 400 false boundaries or none. Real
    detectors use a local statistic - here median + 3*MAD-scaled sigma, floored
    so pure grain never trips it. Sets st.boundary in place.
    """
    diffs = [0.0]
    for a, b in zip(blurs, blurs[1:]):
        diffs.append(float(np.mean(cv2.absdiff(a, b))))
    mid = np.array(diffs[1:], dtype=np.float64) if len(diffs) > 1 else np.array([0.0])
    med = float(np.median(mid))
    mad = float(np.median(np.abs(mid - med))) or 1e-6
    thr = max(4.0, med + 3.0 * 1.4826 * mad)
    for st, d in zip(stats, diffs):
        st.boundary = d > thr
    return stats


def boundaries_from_stats(stats: list[FrameStats], *, dedupe: float = 1.0, min_gap: float = 0.4) -> list[float]:
    """Sorted scene-change times. This map is what makes cuts land on shot
    changes (and what makes the manifest survive a different cut of the film)."""
    raw = [s.t for s in stats if s.boundary]
    out: list[float] = []
    for t in raw:
        if not out or t - out[-1] >= dedupe:
            out.append(round(t, 3))
    # Collapse bursts: film grain / handshake jitter / a handheld shot can fire
    # the detector on 8 consecutive samples. A boundary map with 17 edges in a
    # 50s film makes every snap unpredictable, so keep only the leading edge of
    # each burst. This single rule cut our synthetic test from 17 -> 8 edges.
    tight: list[float] = []
    for b in out:
        if tight and b - tight[-1] < min_gap:
            continue
        tight.append(b)
    return tight


def candidates_from_stats(
    stats: list[FrameStats],
    *,
    min_score: float = 0.42,
    min_seconds: float = 1.2,
    merge_gap: float = 2.0,
) -> list[dict]:
    """Contiguous runs of high-score frames -> candidate 'undressed' segments."""
    runs: list[list[FrameStats]] = []
    cur: list[FrameStats] = []
    for s in stats:
        if s.score >= min_score:
            cur.append(s)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    merged: list[dict] = []
    for r in runs:
        start, end = r[0].t, r[-1].t
        if merged and start - merged[-1]["end"] <= merge_gap:
            merged[-1]["end"] = end
            merged[-1]["scores"].append(np.mean([x.score for x in r]))
        else:
            merged.append({"start": start, "end": end, "scores": [np.mean([x.score for x in r])]})

    out = []
    for m in merged:
        dur = m["end"] - m["start"]
        if dur < min_seconds:
            continue
        peak = float(max(m["scores"]))
        out.append(
            {
                "start": round(m["start"], 3),
                "end": round(m["end"], 3),
                "categories": ["nudity"],
                "confidence": round(min(0.95, 0.35 + 0.65 * peak), 3),
                "label": "vision proxy: sustained skin-dominant shot",
                "source": "vision",
            }
        )
    return out
