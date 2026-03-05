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

> **Note:** R2's ReadableStream yields ~4KB chunks (first chunk 3,493 bytes, then 4,096 bytes each, final chunk 603 bytes — 65 chunks total for 256KB), not the 64KB chunks you might expect.

**Endpoint:** `GET /streaming/{key}`

### Layer 2: FFI double-crossing exhausts Wasm memory (>~10MB) — NOT REPRODUCED

Reading R2 body chunks into Python bytes via `getReader()`, then returning them as a `Response`, crosses the FFI boundary **twice**.  The data exists in three simultaneous copies:

```
             JS side                          Python side
  ┌─────────────────────────┐      ┌──────────────────────────┐
  │  1. R2 body buffer      │──→───│  2. Python bytes          │
  │                         │      │     (full_body = b"...")   │
  │  3. JS Response body  ◄─┼──────│                           │
  └─────────────────────────┘      └──────────────────────────┘

  For 50MB file: ~150MB in Wasm linear memory → Worker crash (in theory)
```

This works fine for small files (256KB = ~768KB peak, well within limits).  For large files (>~10MB), the Worker was expected to crash with a generic error page — no stack trace.

> **Status (2026-03-05):** This bug was **not reproduced** in deployed testing. A 50MB file was returned successfully via `/asgi-full-body/` with HTTP 200 and all 52,428,800 bytes intact. The platform may have increased Wasm memory limits or improved memory management since this issue was originally observed. The workaround (staying on the JS side) is still recommended as a best practice to avoid unnecessary FFI crossings.

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

## Run

```bash
# Deploy (these bugs require real Cloudflare, not Miniflare)
uv run pywrangler deploy

# Seed test data
curl -X POST https://<worker>.workers.dev/seed-small?size_kb=256
curl -X POST https://<worker>.workers.dev/seed?size_mb=50

# Test Bug 1: StreamingResponse truncation (256KB file, returns ~3.5KB first chunk)
curl -s https://<worker>.workers.dev/streaming/test-256kb | wc -c

# Test Bug 2: Memory crash (50MB file)
curl -s https://<worker>.workers.dev/asgi-full-body/test-50mb | wc -c

# Workaround: always works
curl -s https://<worker>.workers.dev/fixed/test-50mb | wc -c

# Diagnostics
curl -s https://<worker>.workers.dev/compare/test-256kb | python -m json.tool
```

## Context

Discovered in [Tasche](https://github.com/AdeWale/tasche), a read-it-later app using Python Workers.  The TTS feature stored audio in R2 and served it via a FastAPI endpoint.  Bug 1 (truncation) was found when `StreamingResponse` silently returned only the first chunk of audio.  Bug 2 (memory crash) was found when MeloTTS returned 49MB WAV files that crashed the Worker on every request.
