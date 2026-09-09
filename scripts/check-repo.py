#!/usr/bin/env python3
"""Publish-readiness gate. Run before the first `git push`.

Checks the things that embarrass people on day one: missing licence, credentials
in the tree, a committed media file that is not the synthetic fixture, broken
workflow YAML, and placeholder strings.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
required = [
    "README.md", "LICENSE", "DATA-LICENSE", "CONTRIBUTING.md", "SECURITY.md",
    "CODE_OF_CONDUCT.md", ".gitignore", "pyproject.toml",
    "sceneguard/schema.py", "sceneguard/skipcore.py", "sceneguard/selftest.py",
    "extension/manifest.json", "extension/core.mjs", "extension/test-core.mjs",
    "extension/fixtures/synthetic.sceneguard.json",
    "android/core/src/main/kotlin/dev/sceneguard/core/SkipCore.kt",
    "android/app/build.gradle.kts", "android/app/src/main/AndroidManifest.xml",
    "desktop/app.py", "scripts/verify-kotlin.sh", "scripts/check-repo.py",
    ".github/workflows/ci.yml", ".github/workflows/android-apk.yml",
    ".github/workflows/desktop-binaries.yml", ".github/pull_request_template.md",
]
secret_re = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}"
    r"|xox[bp]-[A-Za-z0-9-]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
media_re = re.compile(r"\.(mp4|mkv|mov|avi|m4v|ts)$", re.I)
FIXTURE = "extension/fixtures/synthetic.mp4"

fails: list[str] = []
warns: list[str] = []

for rel in required:
    p = ROOT / rel
    if not p.exists() or p.stat().st_size == 0:
        fails.append(f"missing/empty: {rel}")

scanned = 0
for p in sorted(ROOT.rglob("*")):
    if not p.is_file():
        continue
    rel = str(p.relative_to(ROOT))
    if any(x in rel for x in (".git/", "__pycache__", "/build/", ".gradle", "node_modules")):
        continue
    scanned += 1
    if media_re.search(rel) and rel != FIXTURE:
        fails.append(f"real media committed (never do this): {rel}")
    if p.suffix in {".py", ".js", ".mjs", ".kt", ".kts", ".json", ".yml", ".yaml", ".md", ".toml", ".xml", ".srt", ".sh"}:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            warns.append(f"unreadable: {rel} ({e})")
            continue
        for m in secret_re.finditer(text):
            fails.append(f"credential-shaped string in {rel}: {m.group(0)[:18]}…")
        if re.search(r"(?i)(password|passwd|secret|token)\s*[:=]\s*['\"][^'\"\n]{6,}['\"]", text):
            warns.append(f"check for a hardcoded secret: {rel}")
        if "REPLACE" + "_ME" in text and "check-repo" not in rel and "PUBLISHING" not in rel:
            warns.append(f"placeholder left in {rel}")

for y in sorted((ROOT / ".github/workflows").glob("*.yml")):
    try:
        import yaml  # type: ignore

        yaml.safe_load(y.read_text(encoding="utf-8"))
    except ImportError:
        warns.append("PyYAML absent - workflow YAML not parsed here")
    except Exception as e:  # noqa: BLE001
        fails.append(f"invalid YAML in {y.name}: {e}")

for py in sorted(ROOT.rglob("*.py")):
    if ".git" in str(py) or "build" in py.parts:
        continue
    try:
        ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError as e:
        fails.append(f"syntax error in {py.relative_to(ROOT)}: {e}")

# the invariant must be identical on all three surfaces
def grab(path: str, pat: str) -> str | None:
    p = ROOT / path
    if not p.exists():
        return None
    m = re.search(pat, p.read_text(encoding="utf-8"))
    return m.group(1) if m else None


py_inv = grab("sceneguard/schema.py", r'SKIP_ELIGIBLE = \((.*?)\)')
js_inv = grab("extension/core.mjs", r'SKIP_ELIGIBLE = \[(.*?)\]')
kt_inv = grab("android/core/src/main/kotlin/dev/sceneguard/core/SkipCore.kt", r'SKIP_ELIGIBLE = setOf\((.*?)\)')
norm = lambda s: tuple(x.strip().strip('"\'') for x in (s or "").split(",")) if s else None
if not (norm(py_inv) == norm(js_inv) == norm(kt_inv) == ("nudity", "sex")):
    fails.append(f"scope invariant differs: py={norm(py_inv)} js={norm(js_inv)} kt={norm(kt_inv)}")

print(f"scanned {scanned} files")
print(f"  invariant: py={norm(py_inv)} js={norm(js_inv)} kt={norm(kt_inv)}")
for w in warns:
    print("  [warn]", w)
for f in fails:
    print("  [FAIL]", f)
print("\n" + ("READY TO PUBLISH" if not fails else f"{len(fails)} blocker(s) - fix before pushing"))
sys.exit(1 if fails else 0)
