"""
Cue lexicon.

Why this exists instead of "just run a nudity classifier": the single most
reliable, zero-DRM, already-in-the-DOM signal for an explicit scene on any
streaming site is the *caption track*. Platforms serve WebVTT/TTML unencrypted
because the browser has to render it, and Indian OTT dialogue is heavily
expository around exactly the scenes parents object to.

Two India-specific facts baked in here:
  1. Hindi/Marathi profanity is transliterated in Roman script on OTT subs
     ("bc", "mc", "chutiye"), so the lexicon must match the transliteration,
     not just Devanagari.
  2. "Violence is fine, adult is not" is the requirement. So gore/violence cues
     are scored SEPARATELY and never contribute to the nudity/sex buckets.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# word -> (category, weight)
# weight 1.0 = near-certain signal in isolation; 0.5 = needs corroboration.
LEXICON: dict[str, tuple[str, float]] = {
    # ---- sex / nudity (the buckets this user actually wants blocked) ----
    "nude": ("nudity", 1.0),
    "nudity": ("nudity", 1.0),
    "naked": ("nudity", 0.9),
    "topless": ("nudity", 1.0),
    "shirtless": ("nudity", 0.4),
    "strip": ("sex", 0.6),
    "strip club": ("sex", 0.9),
    "undress": ("sex", 0.7),
    "bedroom": ("sex", 0.45),
    "kiss": ("sex", 0.35),
    "kissing": ("sex", 0.35),
    "make love": ("sex", 1.0),
    "sleep with": ("sex", 0.5),
    "one night": ("sex", 0.5),
    "affair": ("sex", 0.3),
    "orgy": ("sex", 1.0),
    "prostitut": ("sex", 0.8),
    "escort": ("sex", 0.5),
    "harass": ("sex", 0.6),
    # transliterated Hindi cues that reliably precede an intimate scene
    "qayamat": ("sex", 0.4),
    "razai": ("sex", 0.4),
    "bistar": ("sex", 0.45),

    # ---- language (own bucket: users often want to MUTE, not skip) ----
    "fuck": ("language", 1.0),
    "fucking": ("language", 1.0),
    "shit": ("language", 0.6),
    "bitch": ("language", 0.6),
    "bastard": ("language", 0.5),
    "asshole": ("language", 0.6),
    "chutiya": ("language", 0.9),
    "chutiyapa": ("language", 0.9),
    "bhenchod": ("language", 1.0),
    "bhosdike": ("language", 1.0),
    "bhosdi": ("language", 0.9),
    "madarchod": ("language", 1.0),
    "maadarchod": ("language", 1.0),
    "randi": ("language", 0.9),
    "gaand": ("language", 0.9),
    "lund": ("language", 0.9),
    "bc": ("language", 0.5),
    "mc": ("language", 0.45),

    # ---- violence / gore (kept, but detected so we can PROVE we kept it) ----
    "blood": ("gore", 0.6),
    "bloodiest": ("gore", 0.7),
    "gut": ("gore", 0.5),
    "guts": ("gore", 0.6),
    "entrails": ("gore", 0.9),
    "sever": ("gore", 0.6),
    "amputat": ("gore", 0.7),
    "tortur": ("gore", 0.8),
    "flay": ("gore", 0.8),
    "dismember": ("gore", 0.9),
    "shoot": ("violence", 0.3),
    "shot": ("violence", 0.3),
    "gun": ("violence", 0.3),
    "kill": ("violence", 0.4),
    "killed": ("violence", 0.4),
    "stab": ("violence", 0.6),
    "behead": ("violence", 0.9),
    "rape": ("sex", 0.7),  # sexual violence -> treat as adult, not as violence
    "molest": ("sex", 0.8),

    # ---- drugs ----
    "joint": ("drugs", 0.4),
    "weed": ("drugs", 0.6),
    "ganja": ("drugs", 0.7),
    "heroin": ("drugs", 0.8),
    "cocaine": ("drugs", 0.8),
    "drunk": ("drugs", 0.3),
    "nashe": ("drugs", 0.6),

    # ---- horror ----
    "ghost": ("horror", 0.3),
    "bhoot": ("horror", 0.4),
    "possessed": ("horror", 0.5),
    "demon": ("horror", 0.4),
}

#: Categories whose cues alone are allowed to create a skip without video
#: corroboration. Everything else needs the vision lane to agree, because a
#: movie about a war cannot have its battle scenes skipped just because the
#: word "kill" appears 40 times.
SKIP_CAPABLE = {"sex", "nudity"}

WORD_RE = re.compile(r"[a-z']+")


@dataclass
class CueHit:
    start: float
    end: float
    text: str
    scores: dict[str, float]

    @property
    def best_category(self) -> str:
        return max(self.scores, key=self.scores.get) if self.scores else ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def score_cue(start: float, end: float, text: str) -> CueHit:
    """Score one caption cue. Multi-word phrases beat single-word matches.

    Score for a category is a soft-OR (1 - product of misses) so five weak cues
    add up but no single cue saturates to 1.0. That property matters: it lets a
    scene be confidently tagged from context while a stray "kiss" line stays
    below the skip threshold.
    """
    low = _norm(text)
    per_cat: dict[str, list[float]] = {}
    for phrase, (cat, w) in LEXICON.items():
        if " " in phrase:
            if phrase in low:
                per_cat.setdefault(cat, []).append(w)
        else:
            if re.search(rf"\b{re.escape(phrase)}\b", low):
                per_cat.setdefault(cat, []).append(w)
    scores = {
        cat: round(1.0 - math.prod(1.0 - min(1.0, w) for w in ws), 3)
        for cat, ws in per_cat.items()
    }
    return CueHit(start=start, end=end, text=text, scores=scores)


# ---------------------------------------------------------------- SRT / VTT
CUE_BLOCK = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
    r"[^\n]*\n(.*?)(?=\n\s*\n|\Z)",
    re.S,
)


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int((ms + "000")[:3]) / 1000.0


def parse_cues(content: str) -> list[tuple[float, float, str]]:
    """Parse SRT or WebVTT into (start, end, text). Handles both comma and dot ms."""
    cues: list[tuple[float, float, str]] = []
    for m in CUE_BLOCK.finditer(content):
        st = _secs(m.group(1), m.group(2), m.group(3), m.group(4))
        en = _secs(m.group(5), m.group(6), m.group(7), m.group(8))
        text = re.sub(r"<[^>]+>", " ", m.group(9))
        text = re.sub(r"\{[^}]*\}", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append((st, max(en, st + 0.4), text))
    return sorted(cues, key=lambda c: c[0])


def hits_from_cues(cues: list[tuple[float, float, str]], min_score: float = 0.45) -> list[CueHit]:
    return [h for h in (score_cue(s, e, t) for s, e, t in cues) if any(v >= min_score for v in h.scores.values())]
