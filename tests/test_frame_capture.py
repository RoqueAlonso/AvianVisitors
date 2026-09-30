import contextlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

from PIL import Image


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPECIES = [{"sci": "Corvus brachyrhynchos", "com": "American Crow", "n": 20}]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status
        self.ok = status == 200

    def json(self):
        return self.data

    def read(self, limit):
        return json.dumps(self.data).encode()[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Route:
    def __init__(self, payload, status=200):
        self.request = types.SimpleNamespace(
            url="http://station/avian/api/birdnet-api.php?action=recent&hours=24&edu=saved",
            headers={},
        )
        self.response = Response(payload, status)
        self.result = None
        self.continued = False

    def fetch(self, **kwargs):
        self.fetch_args = kwargs
        return self.response

    def fulfill(self, **kwargs):
        self.result = kwargs

    def continue_(self):
        self.continued = True


class CapturePage:
    def __init__(self, mode):
        self.mode = mode
        self.routes = {}
        self.screenshots = 0

    def route(self, pattern, handler):
        self.routes[pattern] = handler

    def add_init_script(self, *_args):
        pass

    def goto(self, *_args, **_kwargs):
        self.routes["**/birdnet-api.php**"](Route({"species": SPECIES, "hours": 24}))
        return Response({})

    def wait_for_selector(self, *_args, **_kwargs):
        pass

    def query_selector(self, selector):
        return object() if selector == ".gtile" else None

    def wait_for_function(self, script, **_kwargs):
        if self.mode == "broken-image" or (self.mode == "old-frontend" and "frame" in script):
            raise TimeoutError("collage not ready")
        if self.mode == "decode-timeout" and "decode" in script:
            raise TimeoutError("image decode did not finish")
        if self.mode == "font-timeout" and "fonts.load" in script:
            raise TimeoutError("label font did not finish")
        return types.SimpleNamespace(json_value=lambda: {"token": "1", "revision": "1"})

    def evaluate(self, script, *_args):
        if "fonts.load" in script:
            return True
        if "decode" in script:
            return {"token": "1", "revision": "1"}
        if "frame" in script:
            return not (self.mode == "rerender" and self.screenshots)

    def wait_for_timeout(self, *_args):
        pass

    def screenshot(self, **kwargs):
        self.screenshots += 1
        if "path" in kwargs:
            pathlib.Path(kwargs["path"]).write_bytes(b"new capture")
        return b"new capture"


class FrameCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api = types.ModuleType("playwright.sync_api")
        api.TimeoutError = TimeoutError
        api.sync_playwright = lambda: None
        with mock.patch.dict(sys.modules, {"playwright.sync_api": api}):
            cls.shoot = load("capture_shoot", "frame/shoot.py")
        cls.display = load("capture_display", "frame/display.py")

    def test_recent_errors_are_not_rewritten_as_success(self):
        for payload, status in (({"error": "database unavailable"}, 503),
                                ({"error": "bad data"}, 200),
                                ({"species": None}, 200),
                                ({"species": [None]}, 200),
                                ({"species": [{"sci": "", "n": 1}]}, 200)):
            with self.subTest(payload=payload, status=status):
                route = Route(payload, status)
                self.shoot._make_api_handler(0.04, 12, None)(route)
                self.assertFalse(route.continued)
                self.assertGreaterEqual(route.result["status"], 400)

    def test_valid_empty_response_and_scope_metadata_survive(self):
        route = Route({"species": [], "hours": 12, "educator_scope": {"id": "saved"}})
        self.shoot._make_api_handler(0.04, 12, None)(route)
        self.assertEqual(route.result["status"], 200)
        body = json.loads(route.result["body"])
        self.assertEqual(body["species"], [])
        self.assertEqual(body["educator_scope"], {"id": "saved"})
        self.assertIn("hours=12", route.fetch_args["url"])
        self.assertIn("edu=saved", route.fetch_args["url"])

    def test_floor_does_not_mutate_the_species_snapshot(self):
        species = [dict(SPECIES[0]), {"sci": "Turdus migratorius", "com": "Robin", "n": 1}]
        route = Route({})
        self.shoot._make_api_handler(0.5, 24, None, species)(route)
        self.assertEqual(species[1]["n"], 1)
        self.assertEqual(json.loads(route.result["body"])["species"][1]["n"], 10)

    def test_signature_fetch_rejects_missing_species_not_as_empty(self):
        for payload in ({"error": "database unavailable"}, {"species": None}, []):
            with self.subTest(payload=payload), mock.patch.object(
                    self.display.urllib.request, "urlopen", return_value=Response(payload)):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.display.fetch_recent("http://station", 24, 5)

    def test_capture_failure_preserves_panel_state_and_next_cycle_retries(self):
        cfg = dict(self.display.DEFAULTS)
        state = {"signature": "previous", "last_refresh": 1}
        panel = []
        saves = []
        with mock.patch.object(self.display, "load_state", return_value=state), \
                mock.patch.object(self.display, "fetch_species", return_value=SPECIES), \
                mock.patch.object(self.display, "obtain_image", side_effect=RuntimeError("not ready")) as capture, \
                mock.patch.object(self.display, "push_panel", side_effect=lambda *args: panel.append(args)), \
                mock.patch.object(self.display, "save_state", side_effect=lambda *args: saves.append(args)), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.display.run(cfg)
            self.display.run(cfg)
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(panel, [])
        self.assertEqual(saves, [])

    def test_saved_signature_describes_captured_data_not_earlier_fetch(self):
        cfg = dict(self.display.DEFAULTS, shoot=True)
        captured = [{"sci": "Turdus migratorius", "com": "Robin", "n": 2}]
        saved = []

        def obtain(_cfg, _species, *, capture=None):
            if capture is not None:
                capture["species"] = captured
            return Image.new("RGB", (20, 20))

        with mock.patch.object(self.display, "load_state", return_value={}), \
                mock.patch.object(self.display, "fetch_species", return_value=SPECIES), \
                mock.patch.object(self.display, "obtain_image", side_effect=obtain), \
                mock.patch.object(self.display, "fit_panel", side_effect=lambda image: image), \
                mock.patch.object(self.display, "mat_and_center", side_effect=lambda image, *args: image), \
                mock.patch.object(self.display, "push_panel"), \
                mock.patch.object(self.display, "save_state", side_effect=lambda _path, sig, _now: saved.append(sig)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.display.run(cfg)
        self.assertEqual(saved, [self.display.signature(captured)])

    def capture_page(self, page, output, capture=None):
        browser = mock.Mock()
        browser.new_context.return_value.new_page.return_value = page
        manager = contextlib.nullcontext(types.SimpleNamespace(
            chromium=types.SimpleNamespace(launch=lambda **_kwargs: browser)))
        with mock.patch.object(self.shoot, "sync_playwright", return_value=manager):
            kwargs = {} if capture is None else {"capture": capture}
            return self.shoot.shoot("http://station", output, timeout_ms=5,
                                    bird_names=page.mode == "font-timeout", **kwargs)

    def test_incomplete_or_changed_capture_keeps_previous_png(self):
        for mode in ("broken-image", "old-frontend", "rerender", "decode-timeout", "font-timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = pathlib.Path(directory) / "shot.png"
                output.write_bytes(b"previous capture")
                with self.assertRaises((RuntimeError, TimeoutError)):
                    self.capture_page(CapturePage(mode), str(output))
                self.assertEqual(output.read_bytes(), b"previous capture")

    def test_completed_capture_replaces_png_and_reports_its_species(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "shot.png"
            output.write_bytes(b"previous capture")
            capture = {}
            self.capture_page(CapturePage("ready"), str(output), capture)
            self.assertEqual(output.read_bytes(), b"new capture")
            self.assertEqual(capture["species"], SPECIES)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [output])

    def test_replacing_capture_preserves_existing_file_permissions(self):
        for mode in (0o644, 0o640, 0o600):
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory() as directory:
                output = pathlib.Path(directory) / "shot.png"
                output.write_bytes(b"previous capture")
                output.chmod(mode)
                before = output.stat()
                self.capture_page(CapturePage("ready"), str(output))
                after = output.stat()
                self.assertEqual(stat.S_IMODE(after.st_mode), mode)
                self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))

    def test_new_capture_respects_process_umask(self):
        for mask, mode in ((0o022, 0o644), (0o027, 0o640), (0o077, 0o600)):
            with self.subTest(mask=oct(mask)), tempfile.TemporaryDirectory() as directory:
                output = pathlib.Path(directory) / "shot.png"
                previous_mask = os.umask(mask)
                try:
                    self.capture_page(CapturePage("ready"), str(output))
                finally:
                    os.umask(previous_mask)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), mode)

    def test_capture_does_not_follow_an_output_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "private.png"
            target.write_bytes(b"private image")
            target.chmod(0o600)
            output = pathlib.Path(directory) / "shot.png"
            output.symlink_to(target)
            with self.assertRaises(RuntimeError):
                self.capture_page(CapturePage("ready"), str(output))
            self.assertTrue(output.is_symlink())
            self.assertEqual(target.read_bytes(), b"private image")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
