# Lessons Learned

Structured post-audit findings from this repository. Each entry is a single wide event capturing the full context of what was learned.

---

## 1. Issue 1 (`1-r2-binary`) — Claimed Bug Is Not Real

| Field | Value |
|-------|-------|
| **issue** | `1-r2-binary` |
| **category** | invalid_bug |
| **severity** | high |
| **date_discovered** | 2026-02-26 |
| **claimed_behavior** | `to_js(bytes)` creates a `Uint8Array` view into Wasm memory; heap growth during async ops detaches the `ArrayBuffer`, truncating R2 writes |
| **actual_behavior** | `to_js(bytes)` has created an independent copy via `HEAP8.slice()` since Pyodide 0.17.0 (April 2021). The `.slice()` workaround is redundant. |
| **evidence** | `pyodide/src/core/python2js_buffer.js` — `Module.python2js_buffer_1d_contiguous` uses `HEAP8.slice()`, not `HEAP8.subarray()`. Comment in source: "slice here is a copy (as opposed to subarray which is not)". |
| **upstream_fix** | [pyodide/pyodide#1376](https://github.com/pyodide/pyodide/pull/1376) merged April 2021, included in Pyodide 0.17.0. Cloudflare Workers has always shipped a Pyodide version with this fix. |
| **root_cause** | The claim was based on pre-0.17.0 Pyodide behavior where `to_js()` did return a view. Outdated documentation or blog posts may have been the source. |
| **resolution** | Issue removed from the repository. The `.slice()` call is harmless but unnecessary — it creates a redundant copy. |
| **takeaway** | **Verify upstream behavior before filing.** Trace the actual code path (`to_js` → `_python2js` → `python2js_buffer_1d_contiguous` → `HEAP8.slice()`). Runtime internals change — don't rely on old docs or secondhand claims. |

---

## 2. Issue 2 (`2-fastapi-r2-streaming`) — Model Bug Reproduction

| Field | Value |
|-------|-------|
| **issue** | `2-fastapi-r2-streaming` |
| **category** | valid_bug, exemplary_repro |
| **severity** | info |
| **date_discovered** | 2026-02-26 |
| **what_makes_it_good** | Provides both a broken endpoint (`/stream/{key}`) and a working workaround (`/read/{key}`) side by side. Test asserts the bug manifests (`streamed_size < correct_size`) and includes a self-documenting failure message if the bug is fixed upstream. |
| **pattern** | broken-vs-fixed: always expose both the buggy path and the workaround in the same worker so the test can assert the delta. |
| **takeaway** | **A reproduction that only shows the fix working is not a reproduction.** The test must demonstrate the failure. Issue 2's structure (assert broken < correct) should be the template for all future issues. |

---

## 3. Issue 3 (`3-httpx-headers`) — Valid Bug, Initially Misjudged During Audit

| Field | Value |
|-------|-------|
| **issue** | `3-httpx-headers` |
| **category** | valid_bug, audit_correction |
| **severity** | high |
| **date_discovered** | 2026-02-26 |
| **date_confirmed** | 2026-02-27 |
| **what_happened** | During the initial audit, a sub-agent incorrectly concluded this bug was not real, claiming Cloudflare's `httpx_patch.py` monkey-patches `AsyncClient._send_single_request()` to bypass `jsfetch.py` entirely. We almost removed a valid issue based on this finding. |
| **why_the_audit_was_wrong** | The `httpx_patch.py` in `cloudflare/workerd` is legacy code gated to Pyodide 0.26.0a2 only. For Pyodide 0.27+ (current), the "build step patch" is the `hoodmane/httpx` fork itself — which ships `jsfetch.py` with `HEADERS_TO_IGNORE = ("user-agent",)` baked in. Both `pywrangler dev` and deployed Workers use this fork. |
| **confirmation** | Ran `pywrangler dev` and hit `/test`: httpx dropped `User-Agent`, `js.fetch()` preserved it. Bug reproduces exactly as described. |
| **provenance** | `HEADERS_TO_IGNORE` originated in `koenvo/pyodide-http` ([issue #22](https://github.com/koenvo/pyodide-http/issues/22)) as a browser CORS workaround. It was carried into the `hoodmane/httpx` fork's `jsfetch.py` transport. The upstream PR to `encode/httpx` ([#3330](https://github.com/encode/httpx/pull/3330)) was closed without merge on 2025-02-26, so the fork remains separate. |
| **resolution** | Test added (`test_3_httpx_headers`). Root README updated. Reproduction confirmed live. |
| **takeaway** | **Don't trust sub-agent conclusions about runtime behavior without running the code.** The agent correctly found the monkey-patch file but missed the version gating. A 30-second `curl` against a running worker would have caught the error immediately. |

---

## 4. General — Reproductions Must Reproduce the Bug, Not Just the Fix

| Field | Value |
|-------|-------|
| **category** | design_principle |
| **severity** | high |
| **date_discovered** | 2026-02-26 |
| **observation** | Issue 1 only contained the fixed code path. The buggy code existed only in comments. The test validated the fix worked but could never detect whether the underlying bug was real. |
| **contrast** | Issue 2 exposed both `/stream` (broken) and `/read` (working), allowing the test to assert the difference and self-document when the upstream bug is fixed. |
| **takeaway** | **Structure every reproduction as a differential test.** Expose a broken endpoint and a fixed endpoint. Assert the broken one fails. Include a message like "if this assertion fails, the bug may be fixed upstream" so the test suite becomes a living changelog. |

---

## 5. General — Verify Claims Against Source, Not Documentation

| Field | Value |
|-------|-------|
| **category** | verification_process |
| **severity** | high |
| **date_discovered** | 2026-02-26 |
| **observation** | Issue 1's claim about `to_js()` was plausible and widely repeated, but wrong for the Pyodide version in use. The actual source code (`HEAP8.slice()`) was one click away and unambiguous. |
| **takeaway** | **When filing a runtime bug, trace the implementation.** Read the source of the function you're calling. Documentation lags behind code. A 5-minute source trace would have prevented a bogus issue. |
