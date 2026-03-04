import requests


def test_2_fastapi_r2_streaming(dev_server):
    port = dev_server

    # Seed test data into R2
    seed_resp = requests.post(f"http://localhost:{port}/seed")
    assert seed_resp.status_code == 200
    assert seed_resp.json()["stored_bytes"] == 131072  # 128KB

    # Read using the CORRECT approach (full body via Response)
    read_resp = requests.get(f"http://localhost:{port}/read/test-file")
    assert read_resp.status_code == 200
    correct_size = len(read_resp.content)
    assert correct_size == 131072

    # Read using the WRONG approach (StreamingResponse)
    stream_resp = requests.get(f"http://localhost:{port}/stream/test-file")
    assert stream_resp.status_code == 200
    streamed_size = len(stream_resp.content)

    # StreamingResponse only returns the first chunk.  If this assertion
    # fails, the ASGI adapter bug may have been fixed upstream.
    assert streamed_size < correct_size, (
        f"Expected StreamingResponse to truncate, but got {streamed_size} bytes "
        f"(full size: {correct_size}).  The ASGI adapter bug may be fixed!"
    )

    # Verify the compare endpoint reports the discrepancy
    compare_resp = requests.get(f"http://localhost:{port}/compare/test-file")
    assert compare_resp.status_code == 200
    compare = compare_resp.json()
    assert compare["full_body_size"] == 131072
    assert compare["chunk_count"] > 1


def test_3_httpx_headers(dev_server):
    port = dev_server
    response = requests.get(f"http://localhost:{port}/test")
    assert response.status_code == 200
    result = response.json()

    # httpx should be missing User-Agent due to jsfetch.py HEADERS_TO_IGNORE
    httpx_headers = result["httpx_received"]
    assert "User-Agent" not in httpx_headers, (
        "httpx preserved User-Agent — the jsfetch.py bug may be fixed!"
    )
    assert httpx_headers.get("X-Custom") == "preserved"

    # js.fetch() should preserve both headers
    jsfetch_headers = result["jsfetch_received"]
    assert jsfetch_headers.get("User-Agent") == "repro/1.0"
    assert jsfetch_headers.get("X-Custom") == "preserved"


def test_4_r2_large_binary_roundtrip(dev_server):
    port = dev_server

    # Seed a large binary (50MB)
    seed_resp = requests.post(f"http://localhost:{port}/seed?size_mb=50")
    assert seed_resp.status_code == 200
    key = seed_resp.json()["key"]

    # Fixed path should work — R2 ReadableStream bypasses Python
    fixed_resp = requests.get(f"http://localhost:{port}/fixed/{key}")
    assert fixed_resp.status_code == 200
    fixed_size = len(fixed_resp.content)

    # Broken path should crash or truncate for large binaries
    try:
        broken_resp = requests.get(f"http://localhost:{port}/broken/{key}", timeout=30)
        broken_size = len(broken_resp.content)
        assert broken_size < fixed_size or broken_resp.status_code >= 500, (
            f"Expected broken path to fail for 50MB, but got {broken_size} bytes. "
            f"The Pyodide FFI large binary bug may be fixed!"
        )
    except requests.exceptions.ConnectionError:
        pass  # Worker crashed — expected behavior
