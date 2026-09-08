# Contributing

## The one rule that is not negotiable

**This project skips or blurs nudity and sexual content. It never skips, blurs or
removes violence, gore, blood or language.**

`SKIP_ELIGIBLE = ("nudity", "sex")` in `sceneguard/schema.py` and
`extension/core.mjs` is a hard product invariant, enforced by `enforce_scope()`
in `sceneguard/skipcore.py` as the last step of every producer and consumer.
Violence and profanity are *detected* (so a manifest can prove they were kept, and
so a viewer may mute or mark them) but cannot be cut, not by config, not by a
manifest edit, not by a pull request.

PRs that add a "skip violence" / "remove gore" option will be closed with a link to
this section. That is the whole point of the product: families who are fine with a
fight scene lose the plot the moment you cut it.

## No DRM-adjacent code

No decryption, no key extraction, no frame capture, no stream download, no
re-encode, no re-hosting. If a change requires reading the pixels of a protected
stream, it is out of scope - see `ANDROID.md` §0 for why it is also impossible on
Android, and §1 for the surfaces that are lawful.

## Tests before review

```bash
python -m sceneguard selftest     # 22 checks, includes the scope invariant
node extension/test-core.mjs      # 16 checks against a real generated manifest
```
