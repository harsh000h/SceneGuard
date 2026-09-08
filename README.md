# SceneGuard

**Skips or blurs adult/sexual scenes. Never touches violence, gore or language.**

A parental-control layer for family streaming. Netflix, Prime Video, JioHotstar and
friends can only block *whole titles* by age rating (U / U/A 7+ / 13+ / 16+ / A, as
mandated by India's IT Rules 2021). None of them can skip a single scene. That gap is
the whole project.

`family watching a movie → our manifest says an adult span starts at 01:23:14 →
the player seeks past it (or the frame is mosaicked) → the fight scene carries on
untouched`

[![ci](https://github.com/REPLACE_ME/sceneguard/actions/workflows/ci.yml/badge.svg)](https://github.com/REPLACE_ME/sceneguard/actions)
[![license: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
[![data: CC0](https://img.shields.io/badge/data-CC0-green)](DATA-LICENSE)

> ## What this does **not** do
> No decryption. No frame capture. No downloading, re-encoding or re-hosting of any
> stream. No copies. On a streaming site the only thing we do is call
> `video.currentTime` / `video.muted` - the same gesture as your remote control.
> That boundary is not a disclaimer, it is the design: see `ANDROID.md` §0 for why
> anything beyond it is both impossible on Android (Widevine secure input buffer +
> `FLAG_SECURE`) and the thing that got VidAngel a $9.9M judgment.

## Status

| Surface | State | Runs where |
|---|---|---|
| `sceneguard/` — CLI: analyze / mark / rate | **working, 22 checks green** | your machine |
| `desktop/` — local-file player (skip, blur, mute) | **working, 13 checks green** | Windows / macOS / Linux |
| `extension/` — Chrome MV3 for streaming sites | **working, 16 checks green** | desktop Chrome |
| `android/` — Media3 player for your own files | source + CI, **not yet built on a device** | Android 8+ |

Three implementations of one algorithm (Python / JS / Kotlin) are pinned together by a
golden test: the Android core is checked against the manifest the Python pipeline
writes, so a scene resolves to the same second on every surface.

```bash
python -m sceneguard selftest       # 22/22 - no ffmpeg, no Android SDK, no network
python desktop/app.py selftest      # 13/13 - headless render + policy checks
node extension/test-core.mjs        # 16/16 - against the generated golden manifest
bash scripts/verify-kotlin.sh       #  8/8  - kotlinc only, no Gradle/emulator
python scripts/check-repo.py        # publish gate: licences, secrets, invariant parity
```

## The one rule that cannot be configured away

```python
SKIP_ELIGIBLE = ("nudity", "sex")   # sceneguard/schema.py, extension/core.mjs, SkipCore.kt
```

Violence, gore, blood, language, drugs and horror are **detected** - so a manifest can
*prove* they were kept, and so a viewer may mute or mark them - but they can never be
skipped or blurred. Not by a config flag, not by a hand-edited manifest, not by a fork.
`enforce_scope()` runs as the last step of every producer and consumer, and it *narrows*
a mixed span rather than dropping it (the adult seconds go, the fight stays).

This is the product requirement, and it is enforced where forgetting it is impossible:

```
$ python -m sceneguard analyze x.mp4 --mode skip --blocked violence,gore,nudity,sex
refusing: --blocked {violence,gore} with --mode skip. Only ['nudity', 'sex'] may be
skipped or blurred; violence, gore and language are detect/mute/mark only.
```

## Quick start

```bash
pip install -e .

# 1. Curate timestamps (a human is the only detector that never misses a scene)
sceneguard mark movie.mp4 --start 4993.0 --end 5102.0 --cat sex --verified

# 2. Or draft them from a DRM-free file you own, then confirm
sceneguard analyze movie.mp4 --srt movie.srt --out out/
sceneguard rate out/movie.sceneguard.json      # family-safe card for the whole family

# 3. Play locally with the filter applied
python desktop/app.py play movie.mp4 --manifest out/movie.sceneguard.json

# 4. Streaming: load extension/ as an unpacked Chrome extension.
#    chrome://extensions → Developer mode → Load unpacked → extension/
```

## Installers / binaries

There are **no committed binaries** - GitHub releases reject "trust me" builds, and a
committed `.apk` nobody can audit is a supply-chain risk. Push a tag and CI produces
them from the exact source you are reading:

| Artifact | How | Honest caveats |
|---|---|---|
| `v0.1.0` tag → `app-debug.apk` | `.github/workflows/android-apk.yml` | Debug-signed, debuggable. Play Protect will warn **"app isn't verified"** on install. Fine for your own family; tap More details → Install. Not for Play. |
| same tag → `release.aab` | same workflow, unsigned | Play requires your upload key + Play App Signing. Also: a new personal dev account must run **12 testers × 14 days** closed testing before production. |
| same tag → Windows/macOS/Linux desktop binaries | `.github/workflows/desktop-binaries.yml` | Unsigned → SmartScreen/Gatekeeper warnings. Also *not* a Play Store item, and not a "Netflix app" — it filters **your own files**. |

An APK is an *Android* package. There is no desktop APK; desktop is what the
`desktop-binaries` workflow builds. Desktop streaming filtering = the Chrome extension.

**Play Store:** the local-file Android app is the only publishable piece. An app that
wraps Netflix/Prime/JioHotstar in a WebView is rejected under policy 4.3 (minimum
functionality: *"apps that simply load a website URL… will be rejected"*, and wrapping
a site you don't own counts as spam/trademark). A Chrome extension can never appear on
Play at all — it ships via the Chrome Web Store. Full reasoning in `PUBLISHING.md`.

## Layout

```
sceneguard/          python core: schema, skipcore (merge/snap/resync/scope), analyzer, CLI
  schema.py          the manifest contract - the actual product
  skipcore.py        decide(), enforce_scope(), resync_segments(), merge_overlapping()
  vision.py          local-file detector (PROXY, read its docstring before trusting it)
  lexicon.py         caption lane, incl. transliterated Hindi/Marathi profanity
extension/           Chrome MV3 + core.mjs (pure, tested) - streaming sites, seek/mute only
android/             Gradle project: :core (pure Kotlin, JVM tests) + :app (Media3 player)
desktop/             tkinter/OpenCV local-file app with skip, mosaic blur, mute
ANDROID.md           control surfaces, model choice, DB schema, DRM-free test plan
PUBLISHING.md        licensing, Play policy, tool vs app
```

## Reading order for a new contributor

1. `sceneguard/schema.py` — 90 lines, the data everything shares.
2. `sceneguard/skipcore.py` — the algorithm and the two rules that took the most debugging
   (merge *before* padding; snap with a small search window).
3. `sceneguard/selftest.py` — what "correct" means, in 22 assertions.
4. `CONTRIBUTING.md` — the scope invariant, in words.

## Licence

Code **GPL-3.0-or-later**; timestamp data **CC0**. Deliberate split: the readers should
stay open *and* stay open when forked, while the catalogue only becomes valuable by being
copied, mirrored and argued about. See `PUBLISHING.md` §1.

## Before you push this public

1. `grep -rl "REPLACE_ME" . | xargs sed -i "s/REPLACE_ME/<your-handle>/g"` — badges + `pyproject.toml` URL.
2. Do **not** name the repo after a platform. `sceneguard`, not `netflix-filter`. Platform
   names in prose only; no logos, no studio key art in screenshots.
3. Set the repo's Security policy tab to point at `SECURITY.md`.
4. Expect the first issue to be "add skip-violence". The answer is in `CONTRIBUTING.md`.
