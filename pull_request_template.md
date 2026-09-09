### What changes

### Which core(s) did you touch?
Python `sceneguard/`, JS `extension/core.mjs`, Kotlin `android/core/`. If you changed
decision logic in one, all three must change together and the golden span pairs updated in the
same PR - that is how the surfaces stay in sync.

### Tests (all must be green)
```
python -m sceneguard selftest
python desktop/app.py selftest
node extension/test-core.mjs
bash scripts/verify-kotlin.sh   # or: (cd android && gradle :core:test)
```

### Scope check
- [ ] Cannot cause violence, gore, blood or language to be skipped or blurred
- [ ] Adds no capture, decryption, download or re-encode
