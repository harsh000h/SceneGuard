"""
SceneGuard Skip Manifest - the one file format that everything speaks.

This is deliberately boring JSON. It is the *contract* between:
  - producers  (analyzer, community marking tool, licensed platform feed)
  - consumers  (browser extension, local player, smart-TV companion app)

Why a file format instead of an app: whoever owns the manifest format wins the
category. Netflix/Prime will never ship this, so it has to be open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCHEMA_VERSION = "sceneguard/manifest@1"

#: Filters a viewer can toggle on/off. `keep` categories never cause a skip.
CATEGORIES = (
    "nudity",
    "sex",
    "language",
    "gore",
    "violence",
    "drugs",
    "horror",
)

#: The user story from Kolhapur: violence stays, adult content goes.
DEFAULT_BLOCKED = ("nudity", "sex")

#: HARD INVARIANT. The only categories allowed to remove or obscure picture.
#: violence / gore / language / drugs / horror are DETECTED (so the manifest can
#: prove they were kept, and so a user can mute or mark them) but they can never
#: be skipped or blurred, not by configuration and not by a future contributor
#: who thinks it would be a nice feature. Product rule enforced in code, not in
#: docs - see skipcore.enforce_scope().
SKIP_ELIGIBLE = ("nudity", "sex")

#: What the player does when a category is hit.
#:   skip      - seek past it automatically (aggressive)
#:   ask       - pause once and let the viewer decide (default for weak evidence)
#:   mute      - keep the scene, silence the audio (great for language)
#:   blur      - keep the scene, obscure the frame (needs a render lane: canvas
#:               mosaic over the <video> in a browser, or Media3's
#:              experimentalSetVideoSurface on Android; degrades to skip if absent)
#:   mark_only - draw a marker on the timeline, change nothing (audit / review)
MODES = ("skip", "ask", "mute", "blur", "mark_only")
DEFAULT_MODES = {
    "nudity": "skip", "sex": "skip",
    # these three stay ON PURPOSE: the brief is "parents can watch violence"
    "language": "mute", "gore": "mark_only", "violence": "mark_only",
    "drugs": "mark_only", "horror": "mark_only",
}

#: Skip decision constants
BEFORE = "before"
SKIP = "skip"
AFTER = "after"


@dataclass
class Segment:
    """A span of playback time tagged with one or more content categories."""

    start: float
    end: float
    categories: list[str] = field(default_factory=list)
    confidence: float = 0.5
    label: str = ""
    source: str = "community"
    mode: str = ""  # '' -> resolve from the viewer's per-category modes

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"segment ends before it starts: {self.start} -> {self.end}")
        bad = [c for c in self.categories if c not in CATEGORIES]
        if bad:
            raise ValueError(f"unknown categories: {bad}. allowed: {list(CATEGORIES)}")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 3)
        return d


@dataclass
class Manifest:
    title: str
    video_id: str
    segments: list[Segment] = field(default_factory=list)
    season: int | None = None
    episode: int | None = None
    duration: float | None = None
    source: str = "sceneguard"
    generated_by: str = "unknown"
    license: str = "CC0-1.0"
    schema: str = SCHEMA_VERSION

    # ---------------------------------------------------------------- io
    def to_dict(self) -> dict:
        d = {
            "schema": self.schema,
            "title": self.title,
            "video_id": self.video_id,
            "generated_by": self.generated_by,
            "license": self.license,
            "segments": [s.to_dict() for s in self.segments],
        }
        if self.season is not None:
            d["season"] = self.season
        if self.episode is not None:
            d["episode"] = self.episode
        if self.duration is not None:
            d["duration"] = self.duration
        if self.source != "sceneguard":
            d["source"] = self.source
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(
            title=d["title"],
            video_id=d["video_id"],
            duration=d.get("duration"),
            season=d.get("season"),
            episode=d.get("episode"),
            generated_by=d.get("generated_by", "unknown"),
            source=d.get("source", "sceneguard"),
            segments=[
                Segment(
                    start=float(s["start"]),
                    end=float(s["end"]),
                    categories=list(s.get("categories", [])),
                    confidence=float(s.get("confidence", 0.5)),
                    label=s.get("label", ""),
                    source=s.get("source", "community"),
                )
                for s in d.get("segments", [])
            ],
        )

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------- queries
    def sorted_segments(self) -> list[Segment]:
        return sorted(self.segments, key=lambda s: s.start)

    def segments_for(
        self,
        blocked: tuple[str, ...] | list[str] = DEFAULT_BLOCKED,
        min_confidence: float = 0.0,
        modes: dict[str, str] | None = None,
        action: str = "skip",
    ) -> list[Segment]:
        """Segments this viewer's filter resolves to `action` at/above
        `min_confidence`. Sorted by time.

        A segment's effective action is its own `mode` if set, else the viewer's
        mode for its first matching category. Weak evidence therefore degrades to
        'ask'/'mark_only' instead of silently skipping story beats.
        """
        modes = modes or DEFAULT_MODES
        blocked_set = set(blocked)
        out = []
        for s in self.segments:
            if s.confidence < min_confidence:
                continue
            matched = [c for c in s.categories if c in blocked_set]
            if not matched:
                continue
            eff = s.mode or modes.get(matched[0], "skip")
            if eff == action:
                out.append(s)
        return sorted(out, key=lambda s: s.start)
