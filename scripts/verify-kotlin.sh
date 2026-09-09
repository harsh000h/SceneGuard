#!/usr/bin/env bash
# Verify the Android decision core on a bare JVM - no Android SDK, no Gradle,
# no emulator. Compiles android/core and runs CheckMain against the manifest that
# the Python pipeline itself generated, so the three surfaces are proven to agree.
set -euo pipefail
cd "$(dirname "$0")/.."
FIX=extension/fixtures/synthetic.sceneguard.json
V=extension/fixtures/synthetic.mp4
if [ ! -f "$FIX" ] || [ ! -f "$V" ]; then
  python3 - <<'PY'
import pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from sceneguard.selftest import build_video
import sceneguard.selftest as st
out = pathlib.Path("extension/fixtures"); out.mkdir(parents=True, exist_ok=True)
build_video(out / "synthetic.mp4")
(out / "synthetic.srt").write_text(st.SRT, encoding="utf-8")
subprocess.run([sys.executable, "-m", "sceneguard", "analyze", str(out/"synthetic.mp4"),
                "--srt", str(out/"synthetic.srt"), "--out", str(out)], check=True)
PY
fi
KOTLINC="${KOTLINC:-kotlinc}"
if ! command -v "$KOTLINC" >/dev/null 2>&1; then
  # Fetch a pinned compiler rather than failing: this check guards a cross-language
  # invariant, so "kotlinc not installed" must not become a silent skip.
  echo "kotlinc absent - fetching Kotlin 2.0.21 (pinned) into /tmp" >&2
  if [ ! -x /tmp/kotlinc/bin/kotlinc ]; then
    curl -fsSL -o /tmp/kt.zip https://github.com/JetBrains/kotlin/releases/download/v2.0.21/kotlin-compiler-2.0.21.zip
    (cd /tmp && unzip -q -o kt.zip)
  fi
  KOTLINC=/tmp/kotlinc/bin/kotlinc
  command -v "$KOTLINC" >/dev/null 2>&1 || { echo "still no kotlinc; run: (cd android && gradle :core:test)" >&2; exit 3; }
fi
JAR="$(mktemp -d)/core.jar"
"$KOTLINC" android/core/src/main/kotlin/dev/sceneguard/core/*.kt -include-runtime -d "$JAR" 2>/dev/null
java -cp "$JAR" dev.sceneguard.core.CheckMain "$FIX"
