import pytest
import requests
from echo_server import echo_server

EXPECTED_128KB = 131072


class PlatformBugReproduced(Exception):
    """Raised by a repro test when it observes the known platform bug.

    Tests for active issues are marked ``xfail(strict=True,
    raises=PlatformBugReproduced)``. That makes the outcome loud in both
    directions:

    - the bug reproduces -> xfailed (CONFIRMED in the Results summary);
    - the bug no longer reproduces -> XPASS(strict), which fails the run, the
      signal to move the issue to "Resolved Issues" and drop the marker;
    - anything else goes wrong (server, precondition assertions) -> a normal
      failure, never a silent xfail.

    Tests for resolved issues have no marker, so a regression fails hard.
    """


def active_issue(reason):
    return pytest.mark.xfail(strict=True, raises=PlatformBugReproduced, reason=reason)


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
    # POST /seed stores 32768 big-endian uint32 words where word i holds i.
    # A reordered, repeated or zeroed chunk keeps the length, not the words.
    body = read_resp.content
    words = [int.from_bytes(body[i : i + 4], "big") for i in range(0, len(body), 4)]
    misplaced = next((i for i, word in enumerate(words) if word != i), None)
    assert misplaced is None, f"word {misplaced} holds {words[misplaced]}"

    # Compare endpoint — validates we can read all chunks
    compare_resp = requests.get(f"http://localhost:{port}/compare/test-file")
    assert compare_resp.status_code == 200
    compare = compare_resp.json()
    assert compare["full_body_size"] == EXPECTED_128KB
    assert compare["chunk_count"] > 1

    # StreamingResponse path — resolved upstream by workers-runtime-sdk>=1.1.1.
    # This is now a regression test: truncation must fail the run.
    stream_resp = requests.get(f"http://localhost:{port}/stream/test-file")
    assert stream_resp.status_code == 200
    streamed_size = len(stream_resp.content)
    assert streamed_size == EXPECTED_128KB, (
        f"Regression of resolved issue 2: StreamingResponse returned {streamed_size} "
        f"bytes, expected {EXPECTED_128KB} (ASGI adapter truncated to the first chunk)."
    )


@active_issue(
    "Issue 3: pywrangler-bundled httpx strips User-Agent (jsfetch.py HEADERS_TO_IGNORE)"
)
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
        raise PlatformBugReproduced(
            "Platform bug confirmed: httpx User-Agent header was stripped by "
            "jsfetch.py HEADERS_TO_IGNORE."
        )
    assert httpx_headers.get("User-Agent") == "repro/1.0"


@pytest.fixture
def echo():
    with echo_server() as server:
        yield server


def test_5_sync_http_libraries(dev_server, echo):
    port = dev_server
    echo_url, wire = echo
    response = requests.get(f"http://localhost:{port}/test", params={"echo": echo_url})
    assert response.status_code == 200
    results = response.json()["results"]

    # The echo server's own record is the oracle: each library made exactly
    # one request, and both custom headers arrived on the wire.
    assert sorted(r["query"]["client"][0] for r in wire) == ["requests", "urllib3"]
    for request in wire:
        assert request["headers"].get("User-Agent") == "sync-repro/1.0"
        assert request["headers"].get("X-Custom") == "preserved"

    # The Worker read each library's response body back correctly.
    for client_name in ("requests", "urllib3"):
        client_result = results[client_name]
        assert client_result["status_code"] == 200
        received = client_result["received"]
        assert received.get("User-Agent") == "sync-repro/1.0"
        assert received.get("X-Custom") == "preserved"

    # Default path: both libraries reach a real HTTPS host (httpbin.org).
    live = requests.get(f"http://localhost:{port}/test", timeout=60)
    assert live.status_code == 200
    for client_name in ("requests", "urllib3"):
        received = live.json()["results"][client_name]["received"]
        assert received.get("User-Agent") == "sync-repro/1.0"
        assert received.get("X-Custom") == "preserved"

    # The echo override only reaches loopback, even when urllib.parse and
    # requests/urllib3 would disagree about the host.
    bypass = requests.get(
        f"http://localhost:{port}/test",
        params={"echo": "http://evil.example\\@127.0.0.1/headers"},
    )
    assert bypass.status_code == 400


@active_issue(
    "Issue 4, bug 1: ASGI adapter truncates StreamingResponse to the first chunk"
)
def test_4a_streaming_truncation(deployed_url):
    """Bug 1: ASGI adapter truncates StreamingResponse to first chunk."""
    base = deployed_url

    size_kb = 256
    expected_bytes = size_kb * 1024

    print("\n--- Bug 1: ASGI StreamingResponse truncation ---")
    print(f"Seeding {size_kb}KB test file to R2...")

    seed_resp = requests.post(f"{base}/seed-small?size_kb={size_kb}")
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["stored_bytes"] == expected_bytes
    key = seed_data["key"]
    print(f"  Stored {expected_bytes:,} bytes as '{key}'")

    # /fixed/ — JS bypass workaround, must always work
    print(f"\nGET /fixed/{key}  (JS bypass — data never enters Python)")
    fixed_resp = requests.get(f"{base}/fixed/{key}")
    assert fixed_resp.status_code == 200, f"/fixed/ returned {fixed_resp.status_code}"
    fixed_size = len(fixed_resp.content)
    assert fixed_size == expected_bytes, (
        f"/fixed/ returned {fixed_size} bytes, expected {expected_bytes}."
    )
    print(f"  {fixed_size:,} bytes — OK (workaround works)")

    # /asgi-full-body/ — reads into Python, returns via Response
    print(f"\nGET /asgi-full-body/{key}  (full body read through Python)")
    full_resp = requests.get(f"{base}/asgi-full-body/{key}")
    assert full_resp.status_code == 200, (
        f"/asgi-full-body/ returned {full_resp.status_code} for {size_kb}KB file"
    )
    full_size = len(full_resp.content)
    assert full_size == expected_bytes, (
        f"/asgi-full-body/ returned {full_size} bytes, expected {expected_bytes}."
    )
    print(f"  {full_size:,} bytes — OK (works for small files)")

    # /streaming/ — async generator + StreamingResponse
    print(f"\nGET /streaming/{key}  (async generator + StreamingResponse)")
    stream_resp = requests.get(f"{base}/streaming/{key}")
    assert stream_resp.status_code == 200
    streamed_size = len(stream_resp.content)

    if streamed_size < expected_bytes:
        print(f"  {streamed_size:,} bytes — TRUNCATED (expected {expected_bytes:,})")
        print("\n  BUG 1 CONFIRMED: The ASGI adapter consumed only the first")
        print("  chunk from the async generator and dropped the rest.")
        print("  StreamingResponse is broken for any file that spans")
        print("  multiple R2 chunks (~4KB each).")
        raise PlatformBugReproduced(
            f"Bug 1: StreamingResponse returned {streamed_size} bytes, "
            f"expected {expected_bytes}. ASGI adapter truncates async "
            f"generators to the first yielded chunk."
        )

    print(f"  {streamed_size:,} bytes — OK")
    assert streamed_size == expected_bytes


@active_issue(
    "Issue 4, bug 2: Wasm memory exhausted by FFI round-trip of large R2 objects"
)
def test_4b_memory_crash_probe(deployed_url):
    """Bug 2: Find the Wasm memory crash threshold for this Worker.

    Seeds and round-trips in separate requests (fresh isolate per step)
    at escalating sizes.  A production Worker with many packages crashes at
    ~42MB; this repro Worker has crashed at 50MB.  If it survives every
    step up to 100MB the run fails (XPASS strict): check whether the
    platform changed before moving issue 4 to Resolved Issues.
    """
    base = deployed_url
    step_mb = 10
    max_mb = 100

    last_ok = None
    crash_at = None

    print("\n--- Bug 2: Wasm memory exhaustion from FFI round-trip ---")
    print("Reading R2 data into Python bytes, then returning via Response,")
    print("creates 3 simultaneous copies in Wasm memory. This test finds")
    print("the size at which the Worker crashes.")
    print("\nEscalating R2 round-trip: seed → read into Python → return")

    size_mb = step_mb
    while size_mb <= max_mb:
        expected_bytes = size_mb * 1024 * 1024

        # Seed in its own request
        print(f"\n  {size_mb}MB: seeding...", end="", flush=True)
        seed_resp = requests.post(f"{base}/probe/seed/{size_mb}", timeout=60)
        if seed_resp.status_code >= 500:
            print(f" CRASHED (HTTP {seed_resp.status_code})")
            crash_at = size_mb
            break
        assert seed_resp.status_code == 200, (
            f"Seed failed at {size_mb}MB: HTTP {seed_resp.status_code}"
        )
        print(" ok. round-tripping...", end="", flush=True)

        # Round-trip in its own request (fresh isolate)
        try:
            rt_resp = requests.get(f"{base}/probe/roundtrip/{size_mb}", timeout=60)
        except requests.exceptions.ConnectionError:
            print(" CRASHED (connection reset)")
            crash_at = size_mb
            break

        if rt_resp.status_code >= 500:
            print(f" CRASHED (HTTP {rt_resp.status_code})")
            crash_at = size_mb
            break

        actual = len(rt_resp.content)
        if actual != expected_bytes:
            print(f" TRUNCATED ({actual:,} / {expected_bytes:,} bytes)")
            crash_at = size_mb
            break

        print(f" OK ({actual:,} bytes)")
        last_ok = size_mb
        size_mb += step_mb

    if crash_at is not None:
        print(f"\n  BUG 2 CONFIRMED: Worker crashed at {crash_at}MB.")
        print(f"  Last successful round-trip: {last_ok}MB.")
        print(f"  At {crash_at}MB, Wasm linear memory cannot hold 3 copies")
        print(f"  ({crash_at * 3}MB total: R2 buffer + Python bytes + JS Response).")
        print("  Workers with more packages crash at lower sizes.")
        raise PlatformBugReproduced(
            f"Bug 2: FFI round-trip crashed at {crash_at}MB "
            f"(last success: {last_ok}MB). Wasm memory exhausted by "
            f"3x copies of R2 data crossing the FFI boundary."
        )

    # If no crash, the bug isn't reproducible with this Worker's footprint.
    assert last_ok is not None, "Probe returned no results"
    print(f"\n  No crash up to {max_mb}MB — this Worker's baseline memory")
    print("  footprint is small enough to absorb the 3x copies.")


def test_4c_diagnostics(deployed_url):
    """Diagnostic endpoint validates R2 chunk reading and comparison logic."""
    base = deployed_url

    size_kb = 256
    expected_bytes = size_kb * 1024

    print(f"\n--- Diagnostics: R2 chunk analysis for {size_kb}KB file ---")
    print(f"Seeding {size_kb}KB test file...")

    seed_resp = requests.post(f"{base}/seed-small?size_kb={size_kb}")
    assert seed_resp.status_code == 200
    key = seed_resp.json()["key"]
    print(f"  Stored as '{key}'")

    print(f"\nGET /compare/{key}")
    compare_resp = requests.get(f"{base}/compare/{key}")
    assert compare_resp.status_code == 200
    diag = compare_resp.json()

    assert diag["r2_body_size"] == expected_bytes
    assert diag["chunk_count"] >= 1
    assert diag["fixed_would_return"] == expected_bytes
    assert "ffi_crossings" in diag

    print(f"  R2 body size:    {diag['r2_body_size']:,} bytes")
    print(f"  Chunk count:     {diag['chunk_count']}")
    print(f"  Chunk sizes:     {diag['chunk_sizes']}")
    print("\n  What each endpoint would return:")
    print(
        f"    /streaming/       {diag['streaming_would_return']:,} bytes"
        f"  (first chunk only — Bug 1)"
    )
    print(
        f"    /asgi-full-body/  {diag['full_body_would_return']:,} bytes"
        f"  (all chunks joined)"
    )
    print(f"    /fixed/           {diag['fixed_would_return']:,} bytes  (JS bypass)")
    print("\n  FFI boundary crossings:")
    for path, desc in diag["ffi_crossings"].items():
        print(f"    {path}: {desc}")
