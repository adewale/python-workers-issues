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

---

## 6. Issue 2 (`2-fastapi-r2-streaming`) — Fixed by Runtime SDK 1.1.1

| Field | Value |
|-------|-------|
| **issue** | `2-fastapi-r2-streaming` |
| **category** | upstream_fixed, regression_test |
| **severity** | high |
| **date_discovered** | 2026-02-26 |
| **date_resolved** | 2026-05-01 |
| **original_behavior** | `StreamingResponse` async generators returned only the first R2 `ReadableStream` chunk, truncating responses larger than ~4 KB. |
| **fixed_behavior** | With `workers-runtime-sdk>=1.1.1`, the ASGI adapter consumes all yielded chunks and returns the full response body. |
| **upstream_fix** | [adewale/python-workers-issues#1](https://github.com/adewale/python-workers-issues/pull/1) adds `workers-runtime-sdk>=1.1.1` as a runtime dependency for the reproduction. |
| **confirmation** | After applying the dependency update, `test_2_fastapi_r2_streaming` changed from `xfailed` (`4096` bytes returned instead of `131072`) to `passed`. The overall local result changed from `3 skipped, 2 xfailed` to `1 passed, 3 skipped, 1 xfailed`. |
| **resolution** | Root README moved Issue 2 from active issues to resolved issues. The issue README now documents the fix and keeps the reproduction as a regression test. |
| **takeaway** | **Keep fixed upstream bugs as regression tests when they are cheap to run.** The xfail-on-reproduction pattern naturally flips to a normal pass when the platform fix lands, making the test suite a living changelog. |

---

## 7. CI Reliability — Control the Echo Service, Keep Real HTTP

| Field | Value |
|-------|-------|
| **category** | test_infrastructure, external_dependency |
| **date_resolved** | 2026-10-09 |
| **affected_examples** | `3-httpx-headers`, `5-sync-http-libraries` |
| **observed_failure** | The tests depended on httpbin.org availability. Upstream HTTP failures broke the examples independently of the Ruff upgrade and could obscure the intended header behavior. |
| **resolution** | Keep httpbin as the interactive default, but accept an `ECHO_URL` Worker variable. CI supplies a real loopback HTTP server that echoes and records received headers. Check upstream HTTP errors explicitly. |
| **regression_coverage** | Both clients in each example must reach the echo server. Assertions check actual received headers, not only the Worker's reported JSON. Issue 3's local-request and control-path assertions run before its known-bug xfail. |
| **evidence** | [PR #2](https://github.com/adewale/python-workers-issues/pull/2); merged revision [`17e2a28`](https://github.com/adewale/python-workers-issues/commit/17e2a28d4ac2d5439aaf8470866c5e7a94ededf0); [test and fixture diff](https://github.com/adewale/python-workers-issues/pull/2/files). |
| **confirmed_platform_behavior** | httpx still strips `User-Agent`; `js.fetch()` preserves it. The expected failure describes that reproduced behavior, not an echo-service outage. Both `requests` and `urllib3` preserve the tested headers. |
| **takeaway** | **Control external availability without mocking away the protocol.** Use real HTTP against a controlled service, assert that traffic reaches it, and validate setup before classifying a failure as an expected platform bug. |

---

## 8. Runtime Drift — Import Timing, Tool Versions, and Honest Coverage

| Field | Value |
|-------|-------|
| **category** | runtime_compatibility, verification_process |
| **date_resolved** | 2026-10-09 |
| **observed_failure** | The previous Workers/dependency environment failed during setup. With the updated toolchain, FastAPI telemetry initialization needed entropy unavailable during Worker startup. These failures were not explained by Ruff's code changes. |
| **resolution** | Remove typing-only `webtypy` from runtime dependencies; pin `workers-py` 1.17.7 and CI's uv 0.12.3; select Python 3.13. Move the existing FastAPI app/routes into `src/app.py` and import the app inside the request handler. Terminate the dev-server process group between tests and detect startup-process exits. |
| **tested_environment** | Linux Docker with Python 3.13.14 and Wrangler 4.149.0 locally; clean GitHub Ubuntu PR CI with Python 3.13.15. Ruff and its pre-commit hook are aligned at 0.16.0. Wrangler and other floating layers were not all pinned by this repair. |
| **commands** | `uv sync --dev`; `UV_PYTHON=3.13 CI=true uv run pytest -vv`; `uv run ruff check .`. |
| **validation** | Local and PR CI: **2 passed, 3 skipped, 1 xfailed**, with Ruff passing. [PR CI](https://github.com/adewale/python-workers-issues/actions/runs/37918858835) and [post-merge CI](https://github.com/adewale/python-workers-issues/actions/runs/37919064552) succeeded. |
| **limitations** | The three R2 deployment-only tests were skipped because no deployed Worker URL was supplied. No production deployment was performed. The httpx xfail confirms the still-reproduced header bug; it is not a fix for that platform behavior. The repair did not include a separately documented deliberate-revert negative-control run for every new assertion. |
| **takeaway** | **Compare base and head before blaming a tooling PR; record the full tested environment and coverage boundaries.** A green local suite is not evidence for skipped deployment paths. Pinning one tool does not freeze the whole runtime. |

---

## 9. General — An Imperative `pytest.xfail()` Is Silent in Both Directions

| Field | Value |
|-------|-------|
| **category** | test_design |
| **severity** | high |
| **date_discovered** | 2026-09-27 |
| **observation** | Tests called `pytest.xfail()` inside the test body when the bug reproduced, and passed when it did not. After issue 2 was resolved its test still carried the `xfail` branch, so reverting the fix (dropping `workers-runtime-sdk>=1.1.1`) reported `XFAIL` and the run stayed green. For active issue 3, simulating the upstream fix made the test pass, also green, so nothing prompted moving the README entry. |
| **resolution** | Resolved issues have no xfail: a regression fails the run. Active issues use `@pytest.mark.xfail(strict=True, raises=PlatformBugReproduced)`: the bug reproducing is `XFAIL`, the bug disappearing is `XPASS(strict)` (a failure), and any other error (server start, precondition assertions) is a normal failure instead of a silent xfail. |
| **takeaway** | **A known-bug test must go red when its expectation stops holding, in either direction.** This supersedes the "naturally flips to a normal pass" note in entry 6. |

---

## 10. General — Startup Budgets Must Not Depend on Test Order

| Field | Value |
|-------|-------|
| **category** | flaky_infrastructure |
| **severity** | medium |
| **date_discovered** | 2026-09-27 |
| **observation** | Only `2-fastapi-r2-streaming` got the 300 s CI startup budget; every other directory got 30 s. A cold directory must create its virtualenvs, vendor packages, and fetch wrangler via `npx` before `Ready on`: 28-30 s with warm caches, about 55 s with a cold npm cache. A cold `-k test_3` run failed with `Server failed to start within 30 seconds`. |
| **resolution** | One budget for every directory (`PYWRANGLER_DEV_TIMEOUT`, default 300 s under `CI`), CI warms every directory in its install step, and the server output is drained on a thread so a silent hang still hits the deadline and an early exit fails immediately. The server now runs in its own process group and teardown stops the whole group: terminating only `uv` had left `wrangler` and two `workerd` processes running after every test, which piled up across runs and slowed later cold starts. |
| **takeaway** | **Pay install costs before the timed wait, and give every fixture the same budget.** A timeout tuned to whichever test runs first turns cache warmth into test flakiness. |

Reusable rules are in the
[shared engineering guidance](https://github.com/adewale/python-workers-examples/blob/main/docs/engineering-guidance.md).
The [cross-project retrospective](https://github.com/adewale/python-workers-examples/blob/main/docs/retrospectives/2026-10-09-ruff-rollout.md)
records the 29-PR Ruff rollout, evidence, and separate follow-ups.
