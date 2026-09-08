# Security

SceneGuard is a parental-control tool, so the trust bar is higher than for most utilities.

**Guaranteed by construction**
- No frame capture, no decryption, no re-encoding, no copies. On a streaming site the only
  playback operation is `video.currentTime` / `video.muted` - the remote control, nothing else.
- No telemetry. The extension keeps preferences in `chrome.storage` and calls only the API
  origin you configure, with `credentials: "omit"`. The Android app declares no INTERNET permission.
- No account data: cookies, localStorage, profiles and viewing history are never read.
- Violence, gore and language cannot be cut or blurred by any config, manifest edit or fork
  (`SKIP_ELIGIBLE` + `enforce_scope`/`enforceScope`, tested on all three surfaces).

**Report** a vulnerability privately via the Security advisories tab, not a public issue.
Expect an acknowledgement within 7 days. This is a small project: patches may be slower than a
corporate 90-day SLA, and that is the honest promise rather than an unkept one.

**Known limits that are not bugs**
- A title with no data is not safe. UI must print "no data", never imply "clean".
- Timestamps can drift after a re-cut; resync (`skipcore.resync_segments`) is heuristic.
- Blur is a coarse mosaic (strength 14). A low-radius gaussian is reversible, so we refuse it.
