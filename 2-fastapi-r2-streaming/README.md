# FastAPI R2 Streaming — `StreamingResponse` Truncation Bug

## Status

Resolved by using `workers-runtime-sdk>=1.1.1`. The reproduction is kept as a regression test: current runtimes should return the full R2 object from `StreamingResponse`.

## Original Bug

Older Cloudflare Workers ASGI adapter behavior only consumed the **first yielded chunk** from async generators used in `StreamingResponse`. For R2 content spanning multiple `ReadableStream` chunks (anything over ~4 KB), the response was silently truncated.

```python
# DO NOT DO THIS with R2 bodies:
async def stream_r2(obj):
    reader = obj.body.getReader()
    while True:
        result = await reader.read()
        if result.done:
            break
        yield bytes(result.value.to_py())

return StreamingResponse(stream_r2(obj))  # Only first chunk returned!
```

## Original Workaround

Before the runtime fix, the workaround was to read the full R2 body via `getReader()` and return a plain `Response`:

```python
reader = obj.body.getReader()
parts = []
while True:
    result = await reader.read()
    if result.done:
        break
    parts.append(bytes(result.value.to_py()))

return Response(content=b"".join(parts), media_type="application/octet-stream")
```

## Run

```bash
uv run pywrangler dev
# POST http://localhost:8787/seed          — store 128KB test data
# GET  http://localhost:8787/read/test-file  — full-body response
# GET  http://localhost:8787/stream/test-file — StreamingResponse, now expected to return all bytes
# GET  http://localhost:8787/compare/test-file — inspect R2 chunking
```
