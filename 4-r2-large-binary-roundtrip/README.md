# R2 Binary Round-Trip — Two Pyodide FFI Bugs

Two independent bugs that affect returning R2 data through Python Workers.

## Reproduce

From the repo root:

```bash
uv run pytest tests/test_examples.py -k test_4 --deploy -v -s
```

This deploys the Worker and runs three tests.  Expected output:

```
tests/test_examples.py::test_4a_streaming_truncation
Deploying worker from 4-r2-large-binary-roundtrip/...
Deployed to https://r2-large-binary-roundtrip.<subdomain>.workers.dev
Waiting for worker to be reachable... ready.

--- Bug 1: ASGI StreamingResponse truncation ---
Seeding 256KB test file to R2...
  Stored 262,144 bytes as 'test-256kb'

GET /fixed/test-256kb  (JS bypass — data never enters Python)
  262,144 bytes — OK (workaround works)

GET /asgi-full-body/test-256kb  (full body read through Python)
  262,144 bytes — OK (works for small files)

GET /streaming/test-256kb  (async generator + StreamingResponse)
  3,493 bytes — TRUNCATED (expected 262,144)

  BUG 1 CONFIRMED: The ASGI adapter consumed only the first
  chunk from the async generator and dropped the rest.
  StreamingResponse is broken for any file that spans
  multiple R2 chunks (~4KB each).
XFAIL

tests/test_examples.py::test_4b_memory_crash_probe
--- Bug 2: Wasm memory exhaustion from FFI round-trip ---
Reading R2 data into Python bytes, then returning via Response,
creates 3 simultaneous copies in Wasm memory. This test finds
the size at which the Worker crashes.

Escalating R2 round-trip: seed → read into Python → return

  10MB: seeding... ok. round-tripping... OK (10,485,760 bytes)
  20MB: seeding... ok. round-tripping... OK (20,971,520 bytes)
  30MB: seeding... ok. round-tripping... OK (31,457,280 bytes)
  40MB: seeding... ok. round-tripping... OK (41,943,040 bytes)
  50MB: seeding... CRASHED (HTTP 503)

  BUG 2 CONFIRMED: Worker crashed at 50MB.
  Last successful round-trip: 40MB.
  At 50MB, Wasm linear memory cannot hold 3 copies
  (150MB total: R2 buffer + Python bytes + JS Response).
  Workers with more packages crash at lower sizes.
XFAIL

tests/test_examples.py::test_4c_diagnostics
--- Diagnostics: R2 chunk analysis for 256KB file ---
Seeding 256KB test file...
  Stored as 'test-256kb'

GET /compare/test-256kb
  R2 body size:    262,144 bytes
  Chunk count:     65
  Chunk sizes:     [3493, 4096, 4096, ..., 4096, 603]

  What each endpoint would return:
    /streaming/       3,493 bytes  (first chunk only — Bug 1)
    /asgi-full-body/  262,144 bytes  (all chunks joined)
    /fixed/           262,144 bytes  (JS bypass)

  FFI boundary crossings:
    /asgi-full-body: JS→Python (getReader) then Python→JS (Response) = 2 crossings
    /streaming: ...truncates after first yield
    /fixed: JS→JS (R2 body → Response) = 0 crossings, data never enters Python
PASSED

=================================== Results ====================================
  CONFIRMED  Bug 1: StreamingResponse returned 3493 bytes, expected 262144.
             ASGI adapter truncates async generators to the first yielded chunk.
  CONFIRMED  Bug 2: FFI round-trip crashed at 50MB (last success: 40MB).
             Wasm memory exhausted by 3x copies of R2 data crossing the
             FFI boundary.
  PASSED     test_4c_diagnostics
```

The Bug 2 crash threshold varies between runs (50–60MB for this minimal Worker) depending on GC state and per-isolate memory pressure.  A Worker with more packages crashes at a lower size.

If you already have a deployed Worker, skip the deploy step:

```bash
uv run pytest tests/test_examples.py -k test_4 \
  --deployed-url https://<worker>.workers.dev -v -s
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
