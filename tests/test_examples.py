import requests


def test_1_r2_binary(dev_server):
    port = dev_server
    response = requests.get(f"http://localhost:{port}/store")
    assert response.status_code == 200
    result = response.json()
    assert result["original_size"] == 65536  # 64KB
    assert result["stored_size"] == 65536
    assert result["data_matches"] is True


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
