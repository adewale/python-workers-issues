# R2 Large Binary Round-Trip — Pyodide FFI Crash

## The Bug

Returning large R2 objects (>~10MB) through a Python ASGI response crashes the Worker. The data must cross the Pyodide FFI boundary **twice**:

1. **JS → Python:** R2 `ReadableStream` → Python `bytes` (via `getReader()` / `.arrayBuffer().to_py()`)
2. **Python → JS:** Python `bytes` → ASGI `Response` body → JS `Response`

This doubles memory usage in the Wasm linear memory. For a 49MB WAV file (real-world TTS output from MeloTTS), the Worker crashes with a generic "Worker threw exception" HTML error page — no useful stack trace, no error in browser DevTools.

```python
# DO NOT DO THIS for large R2 objects:
obj = await r2.get(key)
reader = obj.body.getReader()
parts = []
while True:
    result = await reader.read()
    if result.done:
        break
    parts.append(bytes(result.value.to_py()))

full_body = b"".join(parts)
return Response(content=full_body)  # Crashes for >~10MB!
```

## The Workaround

Bypass Python entirely by passing R2's `body` ReadableStream directly to a JS `Response`. The data never enters Python memory:

```python
import js
from pyodide.ffi import to_js

obj = await env.BUCKET.get(key)
headers = to_js({"content-type": "application/octet-stream"})
return js.Response.new(obj.body, headers=headers)
```

This must be done **before** delegating to the ASGI adapter (FastAPI), since ASGI responses always cross the FFI boundary.

## Run

```bash
uv run pywrangler dev
# POST http://localhost:8787/seed?size_mb=50  — generate 50MB test data in R2
# GET  http://localhost:8787/broken/test-50mb — BROKEN: round-trip through Python (crashes)
# GET  http://localhost:8787/fixed/test-50mb  — FIXED: R2 ReadableStream bypasses Python
```

## Context

Discovered in [Tasche](https://github.com/AdeWale/tasche), a read-it-later app using Python Workers. The TTS feature stored audio in R2 and served it via a FastAPI endpoint. MeloTTS returned 49MB WAV files (despite docs claiming MP3), which crashed the Worker on every audio request. The fix was to intercept audio requests in the Worker's `fetch()` method before they reach FastAPI, and pass the R2 ReadableStream directly to a JS Response.
