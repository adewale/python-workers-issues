# Analysis: Why the workers-py maintainer can't reproduce issue #66

Issue: https://github.com/cloudflare/workers-py/issues/66

## TL;DR

The maintainer (@ryanking13) is correct. `to_js(bytes)` in modern Pyodide
**already creates an independent copy** of the data, not a view into Wasm
linear memory. The `.slice()` "fix" in our reproduction is redundant. The bug
as described does not exist.

## The claim in issue #66

> `to_js(bytes)` creates a `Uint8Array` view into Wasm linear memory, not an
> independent copy. If the Wasm heap grows during an async R2 write, the
> underlying `ArrayBuffer` becomes detached and R2 silently writes truncated
> data.

## What actually happens inside Pyodide

Since [PR #1376](https://github.com/pyodide/pyodide/pull/1376) (merged
circa 2021), the `python2js_buffer` code path that handles `to_js(bytes)` uses:

```js
HEAP8.slice(ptr, ptr + byteLength).buffer
```

`HEAP8.slice()` creates a **new `ArrayBuffer`** — an independent copy of the
data. This is explicitly NOT a view into Wasm memory. The old behavior (which
_did_ return a view and caused data corruption — see
[pyodide#749](https://github.com/pyodide/pyodide/issues/749) and
[pyodide#1221](https://github.com/pyodide/pyodide/issues/1221)) was
intentionally fixed by that PR to eliminate use-after-free and
shared-vs-copied-memory inconsistencies.

## Why the maintainer can't reproduce

1. **`to_js()` already copies**: When @ryanking13 removed the `.slice()` call
   and passed the `to_js()` result directly to `r2.put()`, it worked fine
   because `to_js()` already returns an independent copy.

2. **Our reproduction only tests the "fixed" path**: The code in
   `1-r2-binary/src/entry.py` always applies `.slice()`. There is no test case
   that runs WITHOUT `.slice()` to demonstrate the alleged corruption. So the
   reproduction doesn't actually prove the bug exists.

3. **The `.slice()` is redundant**: Calling `.slice()` after `to_js()` just
   copies an already-copied buffer — it has no effect on correctness.

## The API confusion

There are **two different** Pyodide APIs for accessing buffer data, and the
issue conflates them:

| API | Behavior | Safety |
|-----|----------|--------|
| `to_js()` / `toJs()` | Uses `HEAP8.slice()` → creates a **copy** | Safe |
| `getBuffer()` | Returns a **view** into Wasm linear memory | Dangerous |

The `getBuffer()` API _does_ return a view that can become invalid if the Wasm
heap grows or the Python object is garbage-collected. But `to_js()` does not
have this problem — it copies the data.

## What about Cloudflare's custom Pyodide?

Cloudflare uses a [custom build of
Pyodide](https://github.com/cloudflare/pyodide-build-scripts), but:

- The patches in
  [`workerd/src/pyodide/internal/patches/`](https://github.com/cloudflare/workerd/tree/main/src/pyodide/internal/patches)
  only modify `aiohttp.py` and `httpx.py` — no changes to buffer conversion.
- The `pyodide-build-scripts` repo modifies Pyodide for "linking" to workerd
  but does not change core type conversion logic.
- @ryanking13 is on the Cloudflare Python Workers team and would know if the
  fork behaved differently.

## Recommendation

We should update issue #66 to acknowledge that:

1. `to_js(bytes)` already creates a copy in modern Pyodide, so the
   ArrayBuffer-detachment scenario described in the issue cannot occur via this
   code path.
2. The `.slice()` call in our reproduction is redundant and doesn't fix
   anything because there's nothing to fix.
3. If data corruption was actually observed, the root cause is something other
   than `to_js()` returning a view — it would need further investigation under
   different conditions.
4. The `getBuffer()` API (not used in our code) _is_ genuinely dangerous and
   does return a view, so the general concept is valid — but it doesn't apply
   to `to_js()`.

## Sources

- [Pyodide type conversions docs](https://pyodide.org/en/stable/usage/type-conversions.html) — "The `toJs()` API copies the buffer into JavaScript"
- [Pyodide PR #1376](https://github.com/pyodide/pyodide/pull/1376) — Reworked `python2js_buffer` to use copies, fixing soundness issues
- [Pyodide Issue #1221](https://github.com/pyodide/pyodide/issues/1221) — Soundness issues in buffer conversions (use-after-free with views)
- [Pyodide Issue #749](https://github.com/pyodide/pyodide/issues/749) — Bytes corruption bug from old view-based conversion
- [Pyodide core development docs](https://pyodide.org/en/stable/development/core.html) — Describes `python2js_buffer` internals
- [Cloudflare workerd Pyodide source](https://github.com/cloudflare/workerd/tree/main/src/pyodide) — Custom Pyodide integration
- [Cloudflare FFI docs](https://developers.cloudflare.com/workers/languages/python/ffi/) — `to_js` usage documentation
