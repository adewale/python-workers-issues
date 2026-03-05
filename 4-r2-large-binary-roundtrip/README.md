# R2 Binary Round-Trip — Two Pyodide FFI Bugs

Two independent bugs that affect returning R2 data through Python Workers.

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

The `/probe/seed/{mb}` and `/probe/roundtrip/{mb}` endpoints let you find the threshold for any Worker by escalating 10MB at a time.

## Workaround for both bugs

Intercept the request in `fetch()` **before** the ASGI adapter.  Pass R2's `body` ReadableStream directly to a `js.Response`.  Data never enters Python memory:

```python
# In fetch(), before asgi.fetch():
obj = await self.env.BUCKET.get(key)
return js.Response.new(obj.body, headers=to_js({...}))
```

## Reproduce

```bash
# Deploy (these bugs only occur on deployed Workers, not Miniflare)
cd 4-r2-large-binary-roundtrip
uv run pywrangler deploy
```

### Bug 1: StreamingResponse truncation

```bash
# Seed a 256KB file
curl -s -X POST https://<worker>.workers.dev/seed-small?size_kb=256

# StreamingResponse returns ~3.5KB instead of 262,144 bytes
curl -s https://<worker>.workers.dev/streaming/test-256kb | wc -c

# Full-body Response returns all 262,144 bytes (works for small files)
curl -s https://<worker>.workers.dev/asgi-full-body/test-256kb | wc -c

# JS bypass always works
curl -s https://<worker>.workers.dev/fixed/test-256kb | wc -c
```

### Bug 2: Memory crash threshold

```bash
# Escalate 10MB at a time until the Worker crashes
for mb in 10 20 30 40 50 60 70 80 90 100; do
  curl -s -X POST "https://<worker>.workers.dev/probe/seed/$mb" > /dev/null
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://<worker>.workers.dev/probe/roundtrip/$mb")
  echo "${mb}MB: HTTP $STATUS"
  [ "$STATUS" != "200" ] && break
done
```

Expected output for this Worker (FastAPI only, ~8.5MB upload):

```
10MB: HTTP 200
20MB: HTTP 200
30MB: HTTP 200
40MB: HTTP 200
50MB: HTTP 200
60MB: HTTP 500     <-- Worker crashes (error code 1101)
```

A Worker with more packages will crash at a lower size.

### Run the tests

```bash
# From the repo root — deploys the Worker automatically
uv run pytest tests/test_examples.py -k test_4 --deploy -v -s

# Or against an already-deployed Worker
uv run pytest tests/test_examples.py -k test_4 \
  --deployed-url https://<worker>.workers.dev -v -s
```

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /seed-small?size_kb=256` | Seed a small test file |
| `POST /seed?size_mb=50` | Seed a large test file |
| `GET /streaming/{key}` | Bug 1: async generator + StreamingResponse (truncated) |
| `GET /asgi-full-body/{key}` | Full body through Python (works for small, crashes for large) |
| `GET /fixed/{key}` | Workaround: R2 body → JS Response directly |
| `GET /compare/{key}` | JSON diagnostics comparing all paths |
| `POST /probe/seed/{size_mb}` | Seed a probe binary at given size |
| `GET /probe/roundtrip/{size_mb}` | Round-trip probe binary through Python |
