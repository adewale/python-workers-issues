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
    size_mb = 50
    expected_size = size_mb * 1024 * 1024

    # Seed a large binary into the production R2 bucket
    seed_resp = requests.post(f"{base}/seed?size_mb={size_mb}")
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["stored_bytes"] == expected_size
    key = seed_data["key"]

    # Fixed path MUST always work — validates our workaround code
    fixed_resp = requests.get(f"{base}/fixed/{key}")
    assert fixed_resp.status_code == 200, f"/fixed/ returned {fixed_resp.status_code}"
    assert len(fixed_resp.content) == expected_size, (
        f"/fixed/ returned {len(fixed_resp.content)} bytes, expected {expected_size}. "
        f"Our workaround code may be wrong."
    )

    # Broken path — round-trips data through Python memory.
    # If the platform bug is present, this will crash, truncate, or 500.
    # If the platform has fixed the bug, this succeeds identically to /fixed/.
    try:
        broken_resp = requests.get(f"{base}/broken/{key}", timeout=30)
    except requests.exceptions.ConnectionError:
        pytest.xfail(
            "Platform bug confirmed: Worker crashed returning 50MB through Python "
            "ASGI. R2 data crossing the Pyodide FFI boundary twice exceeds Wasm "
            "memory limits."
        )

    if broken_resp.status_code >= 500:
        pytest.xfail(
            f"Platform bug confirmed: /broken/ returned HTTP {broken_resp.status_code}. "
            f"Large R2 round-trip through Python ASGI fails."
        )

    broken_size = len(broken_resp.content)
    if broken_size < expected_size:
        pytest.xfail(
            f"Platform bug confirmed: /broken/ returned {broken_size} bytes, "
            f"expected {expected_size}. Response was truncated."
        )

    assert broken_size == expected_size
