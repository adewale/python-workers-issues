"""FastAPI + R2 streaming example — StreamingResponse truncation bug.

Demonstrates that the Cloudflare Workers ASGI adapter only consumes the
FIRST yielded chunk from async generators used in ``StreamingResponse``.
For R2 content spanning multiple ReadableStream chunks (anything over
~4 KB), the response is silently truncated.

The fix is to read the full R2 body via ``getReader()`` and return a
plain ``Response`` instead of ``StreamingResponse``.

Endpoints:
    POST /seed           — Store 128 KB test data in R2
    GET  /stream/{key}   — WRONG: StreamingResponse (truncated)
    GET  /read/{key}     — CORRECT: Full body via Response
    GET  /compare/{key}  — Show the difference between both approaches
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pyodide.ffi import to_js
from starlette.responses import Response
from workers import WorkerEntrypoint

app = FastAPI()


def _to_py_bytes(js_value):
    """Convert a JS Uint8Array chunk to Python bytes."""
    return bytes(js_value.to_py())


@app.get("/")
async def root():
    return {
        "example": "FastAPI + R2 Streaming",
        "endpoints": {
            "POST /seed": "Store 128KB test data in R2",
            "GET /stream/{key}": "WRONG: StreamingResponse (truncated)",
            "GET /read/{key}": "CORRECT: Full body via Response",
            "GET /compare/{key}": "Compare both approaches",
        },
    }


@app.post("/seed")
async def seed_data(req: Request):
    """Store test data in R2 so we can demonstrate reading it back."""
    env = req.scope["env"]
    r2 = env.BUCKET

    # Create 128KB of test data — large enough to span many ReadableStream
    # chunks (each chunk is typically ~4KB from R2).
    data = bytes(range(256)) * 512  # 128KB repeating pattern

    # Use the .slice() fix from example 16 to write correctly.
    js_view = to_js(data)
    js_owned = js_view.slice()
    await r2.put("test-file", js_owned)

    return {"stored_bytes": len(data), "key": "test-file"}


# --------------------------------------------------------------------------
# BUG: StreamingResponse with async generators is BROKEN in Python Workers.
#
# The Cloudflare Workers ASGI adapter only consumes the FIRST yielded
# chunk from async generators.  If R2 content spans multiple
# ReadableStream chunks (which it does for any file over ~4KB), only
# the first chunk is sent to the client.  The rest is silently dropped.
# --------------------------------------------------------------------------


async def _stream_r2_body(r2_obj):
    """Async generator that yields chunks from an R2 object's body.

    WARNING: This is broken in the Workers ASGI adapter.  Only the first
    yielded chunk will be delivered to the client.
    """
    reader = r2_obj.body.getReader()
    try:
        while True:
            result = await reader.read()
            if result.done:
                break
            chunk = result.value
            if chunk is not None:
                yield _to_py_bytes(chunk)
    finally:
        reader.releaseLock()


@app.get("/stream/{key}")
async def stream_from_r2(key: str, req: Request):
    """WRONG: Serve R2 content via StreamingResponse.

    This will only return the first ~4KB chunk.  The rest is silently
    truncated by the Workers ASGI adapter.
    """
    env = req.scope["env"]
    r2 = env.BUCKET
    obj = await r2.get(key)
    if obj is None:
        return Response(content="Not found", status_code=404)

    # BUG: Only the first chunk is served!
    return StreamingResponse(
        _stream_r2_body(obj),
        media_type="application/octet-stream",
    )


@app.get("/read/{key}")
async def read_from_r2(key: str, req: Request):
    """CORRECT: Read the full R2 body, then return as a plain Response.

    Reads ALL chunks from the ReadableStream into memory, joins them,
    and returns the complete body as a single Response.
    """
    env = req.scope["env"]
    r2 = env.BUCKET
    obj = await r2.get(key)
    if obj is None:
        return Response(content="Not found", status_code=404)

    # Read ALL chunks from the ReadableStream body.
    reader = obj.body.getReader()
    parts = []
    try:
        while True:
            result = await reader.read()
            if result.done:
                break
            chunk = result.value
            if chunk is not None:
                parts.append(_to_py_bytes(chunk))
    finally:
        reader.releaseLock()

    full_body = b"".join(parts)

    return Response(
        content=full_body,
        media_type="application/octet-stream",
        headers={"content-length": str(len(full_body))},
    )


@app.get("/compare/{key}")
async def compare_approaches(key: str, req: Request):
    """Compare the two approaches by reporting chunk details.

    Call ``POST /seed`` first to store test data, then
    ``GET /compare/test-file`` to see what StreamingResponse would lose.
    """
    env = req.scope["env"]
    r2 = env.BUCKET

    obj = await r2.get(key)
    if obj is None:
        return JSONResponse(
            {"error": "Key not found. Call POST /seed first."},
            status_code=404,
        )

    # Read the full body to get the true size and chunk breakdown.
    reader = obj.body.getReader()
    parts = []
    try:
        while True:
            result = await reader.read()
            if result.done:
                break
            chunk = result.value
            if chunk is not None:
                parts.append(_to_py_bytes(chunk))
    finally:
        reader.releaseLock()

    full_body = b"".join(parts)
    first_chunk_size = len(parts[0]) if parts else 0

    return {
        "key": key,
        "full_body_size": len(full_body),
        "chunk_count": len(parts),
        "chunk_sizes": [len(p) for p in parts],
        "streaming_response_would_return": first_chunk_size,
        "bytes_lost": len(full_body) - first_chunk_size,
        "warning": (
            f"StreamingResponse returns only the first chunk "
            f"({first_chunk_size} bytes) out of {len(full_body)} total bytes. "
            f"Use Response instead."
        )
        if len(parts) > 1
        else "Data fits in one chunk — the bug would not manifest at this size.",
        "fix": "Read the full body with getReader(), return a plain Response",
    }


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        import asgi

        return await asgi.fetch(app, request.js_object, self.env)
