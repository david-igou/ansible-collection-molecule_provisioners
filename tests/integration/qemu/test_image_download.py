"""Exercise the QEMU image-cache pipeline against a controlled HTTP server."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


HERE = Path(__file__).parent


@pytest.fixture
def image_server(tmp_path):
    image = tmp_path / "source.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(image), "1M"], check=True)
    payload = image.read_bytes()
    requests = {}
    release_timeout = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests[self.path] = requests.get(self.path, 0) + 1
            attempt = requests[self.path]
            if self.path == "/unavailable" or (self.path == "/flaky" and attempt == 1):
                self.send_error(503, "Temporary image mirror failure")
                return
            if self.path == "/timeout" and attempt == 1:
                release_timeout.wait(2)
            content = b"corrupt image" if self.path == "/corrupt" else payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except BrokenPipeError:
                pass  # Expected when the first request exceeds get_url's timeout.

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", payload, requests
        finally:
            release_timeout.set()
            server.shutdown()
            thread.join()


def run_download(tmp_path, url, payload):
    extra_vars = {
        "mp_qemu_image_cache_dir": str(tmp_path / "cache"),
        "mp_qemu_image_download_timeout": 1,
        "mp_qemu_image_download_retries": 1,
        "_mp_qemu_image": {
            "key": url,
            "value": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        },
    }
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            str(HERE / "assertions" / "run_image_download.yml"),
            "-e",
            json.dumps(extra_vars),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    cached = tmp_path / "cache" / hashlib.sha256(url.encode()).hexdigest() / "disk.qcow2"
    return proc, cached


@pytest.mark.parametrize("endpoint", ["flaky", "timeout"])
def test_download_recovers_and_reuses_cache(tmp_path, image_server, endpoint):
    base_url, payload, requests = image_server
    proc, cached = run_download(tmp_path, f"{base_url}/{endpoint}", payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert requests[f"/{endpoint}"] == 2
    assert cached.read_bytes() == payload
    info = subprocess.run(
        ["qemu-img", "info", "--output=json", str(cached)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(info.stdout)["format"] == "qcow2"


def test_download_stops_when_retries_are_exhausted(tmp_path, image_server):
    base_url, payload, requests = image_server
    proc, cached = run_download(tmp_path, f"{base_url}/unavailable", payload)
    assert proc.returncode != 0
    assert requests["/unavailable"] == 2
    assert "503" in proc.stdout + proc.stderr
    assert not cached.exists()


def test_download_rejects_bad_checksums_after_retry(tmp_path, image_server):
    base_url, payload, requests = image_server
    proc, cached = run_download(tmp_path, f"{base_url}/corrupt", payload)
    assert proc.returncode != 0
    assert requests["/corrupt"] == 2
    assert "checksum" in (proc.stdout + proc.stderr).lower()
    assert not cached.exists()
