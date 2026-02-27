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
