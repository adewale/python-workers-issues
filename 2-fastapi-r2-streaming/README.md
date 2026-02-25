# FastAPI R2 Streaming — `StreamingResponse` Truncation Bug

## The Bug

The Cloudflare Workers ASGI adapter only consumes the **first yielded chunk** from async generators used in `StreamingResponse`. For R2 content spanning multiple `ReadableStream` chunks (anything over ~4 KB), the response is silently truncated.

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

## The Workaround

Read the full R2 body via `getReader()` and return a plain `Response`:

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
# GET  http://localhost:8787/read/test-file  — correct full-body response
# GET  http://localhost:8787/stream/test-file — truncated StreamingResponse
# GET  http://localhost:8787/compare/test-file — see the discrepancy
```
