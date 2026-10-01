"""Real Chromium regression for PNGs that stall after their first rows."""
import functools
import http.server
import importlib.util
import json
import os
from pathlib import Path
import threading
import urllib.parse

import pytest
from PIL import Image

pw = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shooter(monkeypatch):
    spec = importlib.util.spec_from_file_location("browser_shoot", ROOT / "frame/shoot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executable = os.environ.get("FRAME_TEST_CHROMIUM")
    if executable:
        launch = pw.BrowserType.launch
        monkeypatch.setattr(pw.BrowserType, "launch", lambda self, **kw:
                            launch(self, executable_path=executable, **kw))
    return module


@pytest.fixture
def station():
    release = threading.Event()
    started = threading.Event()
    state = {"release_after": None}
    bird = ROOT / "avian/assets/illustrations/corvus-brachyrhynchos.png"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, data, kind):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/":
                html = (ROOT / "avian/frontend/index.html").read_text()
                html = html.replace("</head>", "<script>Math.random=()=>0.9;</script></head>")
                return self.send(html.encode(), "text/html")
            if url.path == "/avian/api/birdnet-api.php":
                data = {}
                if urllib.parse.parse_qs(url.query).get("action") == ["recent"]:
                    data = {"species": [{"sci": "Corvus brachyrhynchos",
                                         "com": "American Crow", "n": 20}], "hours": 24}
                return self.send(json.dumps(data).encode(), "application/json")
            if url.path == "/avian/api/cutout.php":
                data = bird.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                split = len(data) // 12
                self.wfile.write(data[:split])
                self.wfile.flush()
                started.set()
                timer = None
                if state["release_after"] is not None:
                    timer = threading.Timer(state["release_after"], release.set)
                    timer.start()
                try:
                    if release.wait(15):
                        self.wfile.write(data[split:])
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if timer:
                        timer.cancel()
                return
            if url.path.startswith("/avian/api/"):
                return self.send(b"{}", "application/json")
            self.path = self.path.removeprefix("/avian/frontend")
            return super().do_GET()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        Handler, directory=str(ROOT / "avian/frontend")))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", started, state, bird
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join()


def test_stalled_png_keeps_previous_capture(shooter, station, tmp_path, monkeypatch):
    url, started, _state, _bird = station
    output = tmp_path / "shot.png"
    previous = b"previous complete capture"
    output.write_bytes(previous)
    screenshots = []
    screenshot = pw.Page.screenshot

    def record(page, **kwargs):
        screenshots.append(True)
        return screenshot(page, **kwargs)

    monkeypatch.setattr(pw.Page, "screenshot", record)
    with pytest.raises(RuntimeError, match="collage not ready"):
        shooter.shoot(url, str(output), bird_names=True, timeout_ms=3000)
    assert started.is_set(), "the PNG must actually begin transferring"
    assert not screenshots
    assert output.read_bytes() == previous


def test_slow_png_captures_full_bird_after_transfer(shooter, station, tmp_path, monkeypatch):
    url, started, state, bird = station
    state["release_after"] = 0.75
    output = tmp_path / "shot.png"
    screenshot = pw.Page.screenshot
    captured = {}

    def record(page, **kwargs):
        captured.update(page.evaluate("""() => {
          const image = document.querySelector('#collage .gtile img');
          return {complete: image.complete, box: image.getBoundingClientRect().toJSON(),
            labels: document.querySelectorAll('#collage .gtile-label text').length};
        }"""))
        return screenshot(page, **kwargs)

    monkeypatch.setattr(pw.Page, "screenshot", record)
    shooter.shoot(url, str(output), bird_names=True, timeout_ms=10000, dsf=1)
    assert started.is_set()
    assert captured["complete"]
    assert captured["labels"] > 0
    box = captured["box"]
    with Image.open(output) as image, Image.open(bird) as source:
        assert image.size == (600, 800)
        # A partial PNG paints only its first rows. Check the lower half of the
        # actual bird, where neither that strip nor the name can satisfy this.
        width, height = round(box["width"]), round(box["height"])
        expected = source.convert("RGBA").resize((width, height))
        pixels = expected.load()
        expected_ink = sum(pixels[x, y][3] > 200 and max(pixels[x, y][:3]) < 170
                           for y in range(height // 2, height) for x in range(width))
        painted = image.convert("RGB").crop((round(box["x"]), round(box["y"]) + height // 2,
                                              round(box["x"]) + width, round(box["y"]) + height))
        pixels = painted.load()
        actual_ink = sum(max(pixels[x, y]) < 170
                         for y in range(painted.height) for x in range(painted.width))
        assert expected_ink > 100
        assert actual_ink >= expected_ink * 0.8
