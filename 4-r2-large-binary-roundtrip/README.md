# R2 Binary Round-Trip — Two Pyodide FFI Bugs

This Worker demonstrates two **independent** bugs that affect returning R2 data through Python Workers, isolated into separate endpoints so each can be tested and understood on its own.

## Two Bugs, Two Layers

### Layer 1: ASGI adapter truncates StreamingResponse (any size)

The Workers ASGI adapter only consumes the **first** yielded chunk from an async generator passed to `StreamingResponse`.  All subsequent chunks are silently dropped.  This happens at any file size — even 256KB.

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

> **Chunk sizes:** R2's ReadableStream yields ~4KB chunks (first chunk 3,493 bytes, then 4,096 bytes each, final chunk 603 bytes — 65 chunks total for 256KB).

**Endpoint:** `GET /streaming/{key}`

### Layer 2: FFI double-crossing exhausts Wasm memory (large files)

Reading R2 body chunks into Python bytes via `getReader()`, then returning them as a `Response`, crosses the FFI boundary **twice**.  The data exists in three simultaneous copies:

```
             JS side                          Python side
  ┌─────────────────────────┐      ┌──────────────────────────┐
  │  1. R2 body buffer      │──→───│  2. Python bytes          │
  │                         │      │     (full_body = b"...")   │
  │  3. JS Response body  ◄─┼──────│                           │
  └─────────────────────────┘      └──────────────────────────┘

  Total memory ≈ 3× file size in Wasm linear memory
```

The crash threshold depends on the Worker's baseline memory footprint:

| Worker type | Packages | Upload size | Crash threshold |
|-------------|----------|-------------|-----------------|
| **Minimal** (this repo) | FastAPI only | ~small | >50MB (not hit) |
| **Production app** (Tasche) | FastAPI + httpx + BS4 + markdownify (457 modules) | 9.5MB | ~42MB (flaky — 8/10 pass, 2/10 crash) |

> **Why this repo alone can't reproduce it:** A minimal Worker has enough Wasm headroom for 50MB.  A real app with many packages already consumes a significant portion of the Wasm linear memory budget, pushing the crash threshold down.  The `/probe` endpoint finds the threshold for *this* Worker's footprint.

**Endpoint:** `GET /asgi-full-body/{key}`

### The workaround: stay on the JS side

Intercept the request in the Worker's `fetch()` method **before** it reaches the ASGI adapter.  Pass R2's `body` ReadableStream directly to a `js.Response`.  Data never enters Python memory.

```
             JS side (only)
  ┌─────────────────────────┐
  │  R2 obj.body ──→ Response│   0 FFI crossings
  └─────────────────────────┘    Works at any size
```

```python
# In fetch(), before ASGI:
obj = await self.env.BUCKET.get(key)
return js.Response.new(obj.body, headers=to_js({...}))
```

**Endpoint:** `GET /fixed/{key}`

## Endpoints

| Endpoint | What it does | Bug demonstrated |
|----------|-------------|-----------------|
| `POST /seed?size_mb=50` | Store a large binary in R2 | — |
| `POST /seed-small?size_kb=256` | Store a small binary in R2 | — |
| `GET /asgi-full-body/{key}` | Read all chunks into Python, return via `Response` | Bug 2 (memory crash for large files) |
| `GET /streaming/{key}` | Async generator + `StreamingResponse` | Bug 1 (truncation at any size) |
| `GET /compare/{key}` | JSON diagnostics for all paths | — |
| `GET /fixed/{key}` | R2 ReadableStream → JS Response directly | Workaround (always works) |
| `POST /probe/seed/{size_mb}` | Seed a probe binary of given size | — |
| `GET /probe/roundtrip/{size_mb}` | Round-trip probe binary through Python | Bug 2 (crashes at threshold) |

## Run

```bash
# Deploy (these bugs require real Cloudflare, not Miniflare)
uv run pywrangler deploy

# Test Bug 1: StreamingResponse truncation (256KB file, returns ~3.5KB)
curl -X POST https://<worker>.workers.dev/seed-small?size_kb=256
curl -s https://<worker>.workers.dev/streaming/test-256kb | wc -c

# Test Bug 2: Find crash threshold (seed then round-trip, 10MB at a time)
for mb in 10 20 30 40 50 60 70 80 90 100; do
  curl -s -X POST "https://<worker>.workers.dev/probe/seed/$mb" > /dev/null
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://<worker>.workers.dev/probe/roundtrip/$mb")
  echo "${mb}MB: HTTP $STATUS"
  [ "$STATUS" != "200" ] && echo "  ^ Threshold found at ${mb}MB" && break
done

# Workaround: always works at any size
curl -X POST https://<worker>.workers.dev/seed?size_mb=50
curl -s https://<worker>.workers.dev/fixed/test-50mb | wc -c

# Diagnostics
curl -s https://<worker>.workers.dev/compare/test-256kb | python -m json.tool
```

## Cross-validation with Tasche

The crash threshold was verified on [Tasche](https://github.com/adewale/tasche) (a production Python Worker with 457 vendored modules / 9.5MB upload size).  Tasche staging has debug endpoints that exercise the same FFI round-trip:

```bash
BASE="https://tasche-staging.adewale-883.workers.dev"

# Seed and round-trip at escalating sizes
curl -s -X POST "$BASE/api/debug/r2-seed?size_kb=40960"   # 40MB — works
curl -s -X POST "$BASE/api/debug/r2-seed?size_kb=51200"   # 50MB — crashes

curl -s -o /dev/null -w "%{http_code}" "$BASE/api/debug/r2-roundtrip/40960"  # 200
curl -s -o /dev/null -w "%{http_code}" "$BASE/api/debug/r2-roundtrip/51200"  # 500 (error 1101)
```

Results from 2026-03-05 staging testing:

| Size | HTTP | Notes |
|------|------|-------|
| 10MB | 200 | Always works |
| 25MB | 200 | Always works |
| 40MB | 200 | Always works |
| 41MB | 200 | Always works |
| 42MB | 200 | **Flaky** — 8/10 pass, 2/10 crash (error 1101) |
| 45MB | 500 | Always crashes |
| 50MB | 500 | Always crashes |

The threshold is not sharp — it varies with GC state, stack depth, and other per-request allocations.  The flaky zone at ~42MB is consistent with a memory-pressure issue rather than a fixed limit.

## Context

Discovered in [Tasche](https://github.com/adewale/tasche), a read-it-later app using Python Workers.  Bug 1 (truncation) was found when `StreamingResponse` silently returned only the first ~3.5KB chunk of TTS audio.  Bug 2 (memory crash) was found when MeloTTS returned 49MB WAV files that crashed the Worker — but could not be reproduced with this minimal Worker because it lacks the package weight that pushes the memory budget past the threshold.
