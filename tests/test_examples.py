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


def test_4_r2_large_binary_roundtrip(deployed_url):
    base = deployed_url

    # ------------------------------------------------------------------
    # Phase 1: Small file (256KB) — isolates Bug 1 (ASGI truncation)
    # ------------------------------------------------------------------
    size_kb = 256
    expected_small = size_kb * 1024

    seed_resp = requests.post(f"{base}/seed-small?size_kb={size_kb}")
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["stored_bytes"] == expected_small
    small_key = seed_data["key"]

    # /fixed/ must always work — validates our workaround code
    fixed_resp = requests.get(f"{base}/fixed/{small_key}")
    assert fixed_resp.status_code == 200, f"/fixed/ returned {fixed_resp.status_code}"
    assert len(fixed_resp.content) == expected_small, (
        f"/fixed/ returned {len(fixed_resp.content)} bytes, expected {expected_small}."
    )

    # /asgi-full-body/ should work at 256KB — the memory bug only hits large files
    full_resp = requests.get(f"{base}/asgi-full-body/{small_key}")
    assert full_resp.status_code == 200, (
        f"/asgi-full-body/ returned {full_resp.status_code} for {size_kb}KB file"
    )
    assert len(full_resp.content) == expected_small, (
        f"/asgi-full-body/ returned {len(full_resp.content)} bytes, expected {expected_small}. "
        f"This should work for small files — the memory bug only affects >~10MB."
    )

    # /streaming/ — Bug 1: ASGI adapter truncates to first chunk
    stream_resp = requests.get(f"{base}/streaming/{small_key}")
    assert stream_resp.status_code == 200
    streamed_size = len(stream_resp.content)

    if streamed_size < expected_small:
        pytest.xfail(
            f"Bug 1 confirmed: StreamingResponse returned {streamed_size} bytes, "
            f"expected {expected_small}. ASGI adapter truncates async generators "
            f"to the first yielded chunk."
        )
    assert streamed_size == expected_small

    # ------------------------------------------------------------------
    # Phase 2: Large file (50MB) — isolates Bug 2 (Wasm memory crash)
    # ------------------------------------------------------------------
    size_mb = 50
    expected_large = size_mb * 1024 * 1024

    seed_resp = requests.post(f"{base}/seed?size_mb={size_mb}")
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["stored_bytes"] == expected_large
    large_key = seed_data["key"]

    # /fixed/ must always work at any size
    fixed_resp = requests.get(f"{base}/fixed/{large_key}")
    assert fixed_resp.status_code == 200, f"/fixed/ returned {fixed_resp.status_code}"
    assert len(fixed_resp.content) == expected_large, (
        f"/fixed/ returned {len(fixed_resp.content)} bytes, expected {expected_large}. "
        f"Our workaround code may be wrong."
    )

    # /asgi-full-body/ — Bug 2: FFI double-crossing exhausts Wasm memory
    try:
        full_resp = requests.get(f"{base}/asgi-full-body/{large_key}", timeout=30)
    except requests.exceptions.ConnectionError:
        pytest.xfail(
            "Bug 2 confirmed: Worker crashed returning 50MB through Python ASGI. "
            "Three simultaneous copies (R2 buffer + Python bytes + JS Response body) "
            "exceed Wasm memory limits."
        )

    if full_resp.status_code >= 500:
        pytest.xfail(
            f"Bug 2 confirmed: /asgi-full-body/ returned HTTP {full_resp.status_code}. "
            f"Large R2 round-trip through Python exhausts Wasm memory."
        )

    full_size = len(full_resp.content)
    if full_size < expected_large:
        pytest.xfail(
            f"Bug 2 confirmed: /asgi-full-body/ returned {full_size} bytes, "
            f"expected {expected_large}. Response was truncated."
        )

    assert full_size == expected_large

    # ------------------------------------------------------------------
    # Phase 3: Diagnostic endpoint
    # ------------------------------------------------------------------
    compare_resp = requests.get(f"{base}/compare/{small_key}")
    assert compare_resp.status_code == 200
    diag = compare_resp.json()
    assert diag["r2_body_size"] == expected_small
    assert diag["chunk_count"] >= 1
    assert diag["fixed_would_return"] == expected_small
    assert "ffi_crossings" in diag
