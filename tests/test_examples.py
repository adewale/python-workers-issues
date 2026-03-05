import pytest
import requests

EXPECTED_128KB = 131072


def test_2_fastapi_r2_streaming(dev_server):
    port = dev_server

    # Seed test data into R2
    seed_resp = requests.post(f"http://localhost:{port}/seed")
    assert seed_resp.status_code == 200
    assert seed_resp.json()["stored_bytes"] == EXPECTED_128KB

    # Correct approach (full body via Response) — validates our code
    read_resp = requests.get(f"http://localhost:{port}/read/test-file")
    assert read_resp.status_code == 200
    assert len(read_resp.content) == EXPECTED_128KB

    # Compare endpoint — validates we can read all chunks
    compare_resp = requests.get(f"http://localhost:{port}/compare/test-file")
    assert compare_resp.status_code == 200
    compare = compare_resp.json()
    assert compare["full_body_size"] == EXPECTED_128KB
    assert compare["chunk_count"] > 1

    # StreamingResponse path — should return all data, but platform bug
    # causes it to return only the first chunk.
    stream_resp = requests.get(f"http://localhost:{port}/stream/test-file")
    assert stream_resp.status_code == 200
    streamed_size = len(stream_resp.content)

    if streamed_size < EXPECTED_128KB:
        pytest.xfail(
            f"Platform bug confirmed: StreamingResponse returned {streamed_size} bytes, "
            f"expected {EXPECTED_128KB}. ASGI adapter truncates to first chunk."
        )
    assert streamed_size == EXPECTED_128KB


def test_3_httpx_headers(dev_server):
    port = dev_server
    response = requests.get(f"http://localhost:{port}/test")
    assert response.status_code == 200
    result = response.json()

    # js.fetch() should always preserve both headers — validates our code
    jsfetch_headers = result["jsfetch_received"]
    assert jsfetch_headers.get("User-Agent") == "repro/1.0"
    assert jsfetch_headers.get("X-Custom") == "preserved"

    # httpx should also preserve both headers, but platform bug in
    # jsfetch.py strips User-Agent via HEADERS_TO_IGNORE.
    httpx_headers = result["httpx_received"]
    assert httpx_headers.get("X-Custom") == "preserved"

    if "User-Agent" not in httpx_headers:
        pytest.xfail(
            "Platform bug confirmed: httpx User-Agent header was stripped by "
            "jsfetch.py HEADERS_TO_IGNORE."
        )
    assert httpx_headers.get("User-Agent") == "repro/1.0"


def test_4a_streaming_truncation(deployed_url):
    """Bug 1: ASGI adapter truncates StreamingResponse to first chunk."""
    base = deployed_url

    size_kb = 256
    expected_bytes = size_kb * 1024

    # Seed a 256KB test file
    seed_resp = requests.post(f"{base}/seed-small?size_kb={size_kb}")
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["stored_bytes"] == expected_bytes
    key = seed_data["key"]

    # /fixed/ must always work — validates our workaround code
    fixed_resp = requests.get(f"{base}/fixed/{key}")
    assert fixed_resp.status_code == 200, f"/fixed/ returned {fixed_resp.status_code}"
    assert len(fixed_resp.content) == expected_bytes, (
        f"/fixed/ returned {len(fixed_resp.content)} bytes, expected {expected_bytes}."
    )

    # /asgi-full-body/ should work at 256KB — the memory bug only hits large files
    full_resp = requests.get(f"{base}/asgi-full-body/{key}")
    assert full_resp.status_code == 200, (
        f"/asgi-full-body/ returned {full_resp.status_code} for {size_kb}KB file"
    )
    assert len(full_resp.content) == expected_bytes, (
        f"/asgi-full-body/ returned {len(full_resp.content)} bytes, expected {expected_bytes}. "
        f"This should work for small files — the memory bug only affects >~10MB."
    )

    # /streaming/ — Bug 1: ASGI adapter truncates to first chunk (~3.5KB)
    stream_resp = requests.get(f"{base}/streaming/{key}")
    assert stream_resp.status_code == 200
    streamed_size = len(stream_resp.content)

    if streamed_size < expected_bytes:
        pytest.xfail(
            f"Bug 1 confirmed: StreamingResponse returned {streamed_size} bytes, "
            f"expected {expected_bytes}. ASGI adapter truncates async generators "
            f"to the first yielded chunk (~4KB, not 64KB)."
        )
    assert streamed_size == expected_bytes


def test_4b_large_file_memory(deployed_url):
    """Bug 2: FFI double-crossing was expected to exhaust Wasm memory for large files.

    Status (2026-03-05): NOT REPRODUCED. A 50MB file returned successfully
    via /asgi-full-body/ with HTTP 200 and all bytes intact. The platform may
    have increased Wasm memory limits or improved memory management.
    """
    base = deployed_url

    size_mb = 50
    expected_bytes = size_mb * 1024 * 1024

    # Seed a 50MB test file
    seed_resp = requests.post(f"{base}/seed?size_mb={size_mb}")
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["stored_bytes"] == expected_bytes
    large_key = seed_data["key"]

    # /fixed/ must always work at any size
    fixed_resp = requests.get(f"{base}/fixed/{large_key}")
    assert fixed_resp.status_code == 200, f"/fixed/ returned {fixed_resp.status_code}"
    assert len(fixed_resp.content) == expected_bytes, (
        f"/fixed/ returned {len(fixed_resp.content)} bytes, expected {expected_bytes}. "
        f"Our workaround code may be wrong."
    )

    # /asgi-full-body/ — Bug 2: FFI double-crossing was expected to crash,
    # but was NOT REPRODUCED as of 2026-03-05. The test now expects success
    # and will xfail only if the bug reappears.
    try:
        full_resp = requests.get(f"{base}/asgi-full-body/{large_key}", timeout=30)
    except requests.exceptions.ConnectionError:
        pytest.xfail(
            "Bug 2 reproduced: Worker crashed returning 50MB through Python ASGI. "
            "Three simultaneous copies (R2 buffer + Python bytes + JS Response body) "
            "exceed Wasm memory limits."
        )

    if full_resp.status_code >= 500:
        pytest.xfail(
            f"Bug 2 reproduced: /asgi-full-body/ returned HTTP {full_resp.status_code}. "
            f"Large R2 round-trip through Python exhausts Wasm memory."
        )

    full_size = len(full_resp.content)
    if full_size < expected_bytes:
        pytest.xfail(
            f"Bug 2 reproduced: /asgi-full-body/ returned {full_size} bytes, "
            f"expected {expected_bytes}. Response was truncated."
        )

    # If we get here, the bug is NOT reproduced — 50MB returned successfully
    assert full_size == expected_bytes


def test_4c_diagnostics(deployed_url):
    """Diagnostic endpoint validates R2 chunk reading and comparison logic."""
    base = deployed_url

    size_kb = 256
    expected_bytes = size_kb * 1024

    # Seed a small file for diagnostics (may already exist from test_4a)
    seed_resp = requests.post(f"{base}/seed-small?size_kb={size_kb}")
    assert seed_resp.status_code == 200
    key = seed_resp.json()["key"]

    compare_resp = requests.get(f"{base}/compare/{key}")
    assert compare_resp.status_code == 200
    diag = compare_resp.json()
    assert diag["r2_body_size"] == expected_bytes
    assert diag["chunk_count"] >= 1
    assert diag["fixed_would_return"] == expected_bytes
    assert "ffi_crossings" in diag
