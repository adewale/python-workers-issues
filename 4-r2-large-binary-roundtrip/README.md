# R2 Binary Round-Trip — Two Pyodide FFI Bugs

Two independent bugs that affect returning R2 data through Python Workers.

## Reproduce

From the repo root:

```bash
uv run pytest tests/test_examples.py -k test_4 --deploy -v -s -rX
```

This deploys the Worker and runs three tests.  Expected output:

```
tests/test_examples.py::test_4a_streaming_truncation XFAIL
tests/test_examples.py::test_4b_memory_crash_probe
  Probe results:
    10MB: OK (10485760 bytes)
    20MB: OK (20971520 bytes)
    30MB: OK (31457280 bytes)
    40MB: OK (41943040 bytes)
    50MB: OK (52428800 bytes)
    60MB: seed crashed (HTTP 503)

    Last successful: 50MB
    Crashed at: 60MB
  XFAIL
tests/test_examples.py::test_4c_diagnostics PASSED

========================= XFAIL summary ===========================
XFAILED test_4a - Bug 1 confirmed: StreamingResponse returned 3493
    bytes, expected 262144. ASGI adapter truncates async generators
    to the first yielded chunk (~4KB).
XFAILED test_4b - Bug 2 confirmed: FFI round-trip crashed at 60MB
    (last success: 50MB). Wasm memory exhausted by 3x copies of R2
    data crossing the FFI boundary.
============ 1 passed, 2 xfailed in ~180s ========================
```

The Bug 2 threshold varies between runs (50–60MB for this minimal Worker).  A Worker with more packages crashes at a lower size.

If you already have a deployed Worker, skip the deploy step:

```bash
uv run pytest tests/test_examples.py -k test_4 \
  --deployed-url https://<worker>.workers.dev -v -s -rX
```

## Bug 1: ASGI adapter truncates StreamingResponse

The Workers ASGI adapter only consumes the **first** yielded chunk from an async generator passed to `StreamingResponse`.  All subsequent chunks are silently dropped.  This happens at any file size.

```
   R2 ReadableStream          Python async generator       ASGI adapter
  ┌──────────────────┐       ┌──────────────────────┐     ┌─────────────┐
  │ chunk 1 (~4KB) ──┼──→────│ yield chunk 1    ────┼──→──│ ✓ consumed  │
  │ chunk 2 (~4KB) ──┼──→────│ yield chunk 2    ────┼──→──│ ✗ DROPPED   │
  │ ...              │       │ ...                  │     │             │
  │ chunk 65 (~1KB)──┼──→────│ yield chunk 65   ────┼──→──│ ✗ DROPPED   │
  └──────────────────┘       └──────────────────────┘     └─────────────┘
                                                           Response: ~3.5KB
                                                           Expected: 256KB
```

R2's ReadableStream yields ~4KB chunks (first chunk 3,493 bytes, then 4,096 bytes each).  Only the first chunk survives.

**test_4a** seeds a 256KB file and hits three endpoints:
- `/fixed/` returns all 262,144 bytes (JS bypass, workaround)
- `/asgi-full-body/` returns all 262,144 bytes (full-body `Response`, works for small files)
- `/streaming/` returns only ~3,493 bytes (`StreamingResponse`, **truncated**)

## Bug 2: FFI round-trip exhausts Wasm memory for large files

Reading R2 body chunks into Python bytes, then returning via `Response`, crosses the FFI boundary **twice**.  The data exists in three simultaneous copies:

```
             JS side                          Python side
  ┌─────────────────────────┐      ┌──────────────────────────┐
  │  1. R2 body buffer      │──→───│  2. Python bytes          │
  │                         │      │     (full_body = b"...")   │
  │  3. JS Response body  ◄─┼──────│                           │
  └─────────────────────────┘      └──────────────────────────┘

  Total memory ≈ 3× file size in Wasm linear memory
```

The crash threshold depends on the Worker's baseline memory footprint — how much of the Wasm linear memory is already consumed by packages.  This Worker is minimal (FastAPI only), so the threshold is high (~60MB).  A Worker with more packages (httpx, beautifulsoup4, etc.) will crash at lower sizes.

**test_4b** seeds and round-trips files at 10MB, 20MB, 30MB, ... in separate requests (each gets a fresh isolate) until one crashes with HTTP 503 / error code 1101.

## Workaround for both bugs

Intercept the request in `fetch()` **before** the ASGI adapter.  Pass R2's `body` ReadableStream directly to a `js.Response`.  Data never enters Python memory:

```python
# In fetch(), before asgi.fetch():
obj = await self.env.BUCKET.get(key)
return js.Response.new(obj.body, headers=to_js({...}))
```

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /seed-small?size_kb=256` | Seed a small test file |
| `POST /seed?size_mb=50` | Seed a large test file |
| `GET /streaming/{key}` | Bug 1: StreamingResponse truncated to first chunk |
| `GET /asgi-full-body/{key}` | Full body through Python (works for small, crashes for large) |
| `GET /fixed/{key}` | Workaround: R2 body → JS Response directly |
| `GET /compare/{key}` | JSON diagnostics comparing all paths |
| `POST /probe/seed/{size_mb}` | Seed a probe binary at given size |
| `GET /probe/roundtrip/{size_mb}` | Round-trip probe binary through Python |
