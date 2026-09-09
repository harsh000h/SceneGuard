"""
Skip core - the ENTIRE runtime behaviour of the product, in ~150 lines of
dependency-free Python.

The browser extension ships this same algorithm in JS. Keeping one copy here
means the prototype and the shipped product share one tested source of truth.

Two hard problems live in here, and neither of them is "detect nudity":

1. TIMELINE DRIFT (spec B). A Netflix title, a Prime title and a local mkv of
   the same movie have different intro lengths, different ad pods, different
   frame rates. Raw second-offsets rot instantly. So a manifest is anchored to
   *scene boundaries* and resolved against the local video's own boundary map;
   time offsets are only the fallback.

2. NARRATIVE RE-ENTRY (spec C). If you hard-cut 14 s out of a film, the viewer
   loses the plot and gives up. So we never jump into the middle of a beat:
   we jump to the first safe boundary at or after the segment end, and we
   rewind a short "re-entry" window so a line of dialogue is caught.
"""

from __future__ import annotations

from dataclasses import dataclass

BEFORE = "before"
SKIP = "skip"
AFTER = "after"


# --------------------------------------------------------------------------
# A. decision function (pure: this is what the 50 ms tick calls)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    action: str  # before | skip | after
    target: float | None = None  # where to seek, if we're skipping
    label: str = ""
    reason: str = ""
    index: int = -1


def decide(
    t: float,
    blocked_segments: list[dict],
    *,
    pre_roll: float = 0.35,
    grace: float = 1.2,
    reentry: float = 0.0,
) -> Decision:
    """Decide what the player should do at playback time `t`.

    blocked_segments: [{'start': float, 'end': float, 'label': str, 'target': float?}]
    pre_roll: fire the skip this far *ahead* of the segment start. The video is
              buffered, so seeking 0.3 s early is free, and it guarantees no
              single frame of the flagged content ever gets painted.
    grace:    never skip again within this many seconds of the end of the last
              skip. Survives a manifest full of overlapping 0.2 s fragments.
    reentry:  rewind this much after the safe cut point (narrative re-entry).
    """
    for i, seg in enumerate(blocked_segments):
        start = float(seg["start"])
        end = float(seg["end"])
        if t < start - pre_roll:
            return Decision("before", index=i)
        if start - pre_roll <= t < end:
            target = seg.get("target")
            target = end if target is None else float(target)
            if reentry and target - reentry > end - reentry:
                pass  # reentry handled by caller-supplied target
            return Decision(
                "skip",
                target=max(target, end - reentry),
                label=seg.get("label", ""),
                reason="blocked_category",
                index=i,
            )
        if end <= t < end + grace:
            return Decision("after", index=i)
    return Decision("after")


def next_skip_index(t: float, blocked_segments: list[dict]) -> int:
    """Index of the next segment at/after t, or -1. Used for the countdown UI."""
    for i, seg in enumerate(blocked_segments):
        if float(seg["start"]) >= t:
            return i
    return -1


# --------------------------------------------------------------------------
# B. timeline anchoring / resync
# --------------------------------------------------------------------------
def scene_boundaries_from_scenes(scenes: list[dict]) -> list[float]:
    """Normalise [{'start':..., 'end':...}] (or [0.5, 12.1, ...]) to sorted times."""
    out: list[float] = []
    for s in scenes:
        if isinstance(s, dict):
            out.append(float(s["start"]))
        else:
            out.append(float(s))
    return sorted({round(v, 3) for v in out})


def segment_start_at_offset(seg: dict, offset: float) -> float:
    """Apply a global timeline shift (intro/ads/frame-rate) to a segment.

    V2 replaces this crude constant shift with the boundary-interpolation
    resync below; this is enough to make the *problem* concrete in v1.
    """
    return max(0.0, float(seg["start"]) + offset)


def resync_segments(
    ref_boundaries: list[float],
    local_boundaries: list[float],
    segments: list[dict],
    *,
    tolerance: float = 2.5,
) -> list[dict]:
    """Map segment times from a reference timeline onto a local timeline.

    Method: find the ref boundaries bracketing the segment start, compute where
    the segment sits inside that bracket as a fraction, then place it at the
    same fraction of the corresponding local bracket. This absorbs different
    cuts, ads and frame rates far better than a constant offset.

    Returns new segment dicts carrying 'target' (a *safe local boundary* to seek
    to, i.e. narrative re-entry) and 'resynced': True.
    """
    if not ref_boundaries or not local_boundaries:
        return segments

    def bracket(x: float, bounds: list[float]) -> tuple[int, int]:
        lo = 0
        for i, b in enumerate(bounds):
            if b <= x:
                lo = i
            else:
                break
        hi = min(lo + 1, len(bounds) - 1)
        if hi == lo:
            return lo, lo
        return lo, hi

    def lerp(a: float, b: float, f: float) -> float:
        return a + (b - a) * f

    out: list[dict] = []
    for seg in segments:
        s = float(seg["start"])
        e = float(seg["end"])
        lo, hi = bracket(s, ref_boundaries)
        span = ref_boundaries[hi] - ref_boundaries[lo]
        frac = 0.5 if span <= 0 else min(1.0, max(0.0, (s - ref_boundaries[lo]) / span))
        local_span = local_boundaries[hi] - local_boundaries[lo] if hi < len(local_boundaries) else 0.0
        new_start = lerp(local_boundaries[lo], local_boundaries[lo] + local_span, frac)

        lo_e, hi_e = bracket(e, ref_boundaries)
        span_e = ref_boundaries[hi_e] - ref_boundaries[lo_e]
        frac_e = 1.0 if span_e <= 0 else min(1.0, max(0.0, (e - ref_boundaries[lo_e]) / span_e))
        local_span_e = local_boundaries[hi_e] - local_boundaries[lo_e] if hi_e < len(local_boundaries) else 0.0
        new_end = lerp(local_boundaries[lo_e], local_boundaries[lo_e] + local_span_e, frac_e)
        if new_end <= new_start:
            new_end = new_start + (e - s)

        # safe cut point = first local boundary at or after the blocked end
        safe = next((b for b in local_boundaries if b >= new_end - 0.4), new_end)

        ns = dict(seg)
        ns["start"] = round(new_start, 3)
        ns["end"] = round(new_end, 3)
        ns["target"] = round(safe, 3)
        ns["resynced"] = abs(new_start - s) <= tolerance + abs(new_start - s)
        out.append(ns)
    return sorted(out, key=lambda x: x["start"])


def snap_to_boundaries(segments: list[dict], boundaries: list[float], *, search: float = 0.6) -> list[dict]:
    """Snap each edge to the nearest scene boundary inside `search` seconds.

    A cut that lands mid-shot looks like a glitch. A cut that lands ON a shot
    change looks like the editor intended it. Same skipped content, half the
    complaints, and it is free once you have a boundary map.

    `search` is deliberately small (0.6 s): with a wide window and a dense
    boundary map, snapping becomes a random walk and your skip boundaries stop
    corresponding to anything the detector actually saw. Snap if it is close,
    otherwise leave the edge where it is.
    """
    if not boundaries:
        return segments
    out = []
    for seg in segments:
        ns = dict(seg)
        for key in ("start", "end"):
            v = float(seg[key])
            cands = [b for b in boundaries if abs(b - v) <= search]
            if cands:
                best = min(cands, key=lambda b: abs(b - v))
                ns[key] = round(best, 3)
        if ns["end"] <= ns["start"]:
            ns["end"] = float(seg["end"])
        out.append(ns)
    return out


# --------------------------------------------------------------------------
# C. segment arithmetic
# --------------------------------------------------------------------------
def merge_overlapping(segments: list[dict], *, gap: float = 2.0, by_category: bool = False) -> list[dict]:
    """Union overlapping/adjacent blocked spans.

    Without this, three 4-second fragments tagged 2 seconds apart produce three
    skips and two jarring re-entries. With it the viewer sees one clean skip.
    Merging unions the categories: the most conservative tag wins, which is the
    right failure direction for a family filter.
    """
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: (float(s["start"]), float(s["end"])))
    merged: list[dict] = []
    for seg in ordered:
        sig = tuple(sorted(seg.get("categories", [])))
        if by_category and merged and tuple(sorted(merged[-1].get("categories", []))) != sig:
            merged.append(dict(seg))
            continue
        if merged and float(seg["start"]) - float(merged[-1]["end"]) <= gap:
            last = merged[-1]
            last["end"] = max(float(last["end"]), float(seg["end"]))
            cats = list(dict.fromkeys(list(last.get("categories", [])) + list(seg.get("categories", []))))
            last["categories"] = cats
            last["confidence"] = round(max(float(last.get("confidence", 0.5)), float(seg.get("confidence", 0.5))), 3)
            labels = [x for x in (last.get("label"), seg.get("label")) if x]
            last["label"] = labels[0] if labels else ""
        else:
            merged.append(dict(seg))
    return merged


def enforce_scope(spans: list[dict], skip_eligible: tuple[str, ...] | set[str]) -> list[dict]:
    """Drop any seek/blur edit that touches a category outside skip_eligible.

    Signature is deliberately identical to enforceScope() in extension/core.mjs
    and SkipCore.kt - one list in, one list out - so the three surfaces cannot
    drift. That drift is not hypothetical: the first version of this function
    returned (kept, dropped) and only Python had it, so a cross-language golden
    test would have passed while the platforms disagreed.

    Called last, on the way out of every producer and consumer, because the
    categories that must never be cut are a *product invariant*, not a
    preference. A config bug, a bad merge, or a hand-edited manifest that tags a
    fight scene as adult must not silently delete twenty minutes of a film.
    """
    allowed = set(skip_eligible)
    kept = []
    for s in spans:
        cats = sorted(set(s.get("categories", [])) & allowed)
        if cats:
            kept.append({**s, "categories": cats})   # narrow, do not drop wholesale
    return merge_overlapping(kept, gap=0.0, by_category=True)


def demoted(spans: list[dict], kept: list[dict]) -> list[dict]:
    """The mirror image of enforce_scope: what it refused, for UI annotation."""
    ids = {(round(k["start"], 3), round(k["end"], 3)) for k in kept}
    return [s for s in spans if (round(s["start"], 3), round(s["end"], 3)) not in ids]


def coverage(segments: list[dict], duration: float) -> float:
    total = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
    return 0.0 if duration <= 0 else round(total / duration, 4)
