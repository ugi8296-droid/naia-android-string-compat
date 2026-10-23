"""Real loopback auth and pinned source adapters, without external network."""
import http.client
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT.parent / "source" / "NAIA2.0"
sys.path.insert(0, str(ROOT))
import naia_tools_ui as ui


class Controller:
    def __init__(self):
        self.calls = []
        self.ctx = types.SimpleNamespace(_app_context=types.SimpleNamespace(repo_root=UPSTREAM))
    def status(self):
        return {"settings": {"low_memory": True, "cache_mb": 32}, "memory": {"revision": 1}, "paired": False}
    def update_settings(self, data):
        self.calls.append(("settings", data)); return self.status()
    def import_pairing(self, data):
        self.calls.append(("pair", data)); return self.status()
    def disconnect(self):
        self.calls.append(("disconnect", None)); return self.status()
    def open_folder(self, target):
        self.calls.append(("folder", target)); return {"opened": False}
    def open_relay(self):
        self.calls.append(("relay", None)); return {"opened": False, "url": "https://naia-tablet-link.ugi8296.chatgpt.site"}


class ToolsUITests(unittest.TestCase):
    def request(self, server, method, path, body=None, *, auth=True, headers=None):
        merged = {"Host": server.authority}
        if auth:
            merged["X-NAIA-Android-Tools"] = server.token
        if method == "POST":
            merged.update({"Origin": server.origin, "Content-Type": "application/json"})
        merged.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", server.server.server_port, timeout=3)
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=merged)
        response = connection.getresponse()
        result = response.read().decode()
        code, response_headers = response.status, dict(response.getheaders())
        connection.close()
        return code, result, response_headers

    def test_loopback_capability_origin_host_and_route_boundaries(self):
        controller = Controller(); server = ui.PanelServer(controller); self.addCleanup(server.close)
        code, body, headers = self.request(server, "GET", "/", auth=False)
        self.assertEqual(code, 200); self.assertNotIn(server.token, body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request(server, "GET", "/status", auth=False)[0], 403)
        self.assertEqual(self.request(server, "GET", "/status?token="+server.token, auth=False)[0], 403)
        self.assertEqual(self.request(server, "GET", "/status", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request(server, "OPTIONS", "/pair")[0], 403)
        for override in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": ""},
                         {"Host": "evil.example"}, {"X-NAIA-Android-Tools": "wrong"}, {"Content-Type": "text/plain"}):
            with self.subTest(override=override):
                self.assertIn(self.request(server, "POST", "/pair", {}, headers=override)[0], (403, 415))
        self.assertEqual(controller.calls, [])
        self.assertEqual(self.request(server, "POST", "/pair", ["bad"])[0], 400)
        self.assertEqual(self.request(server, "POST", "/settings", {"x": "x"*66000})[0], 400)
        self.assertEqual(self.request(server, "POST", "/folder/open", {"target": "/private"})[0], 400)
        for path, data in (("/settings", {"low_memory": False}), ("/pair", {"opaque": "example"}),
                           ("/disconnect", {}), ("/folder/open", {"target": "save"}), ("/open-relay", {})):
            self.assertEqual(self.request(server, "POST", path, data)[0], 200)
        self.assertEqual(len(controller.calls), 5)
        self.assertEqual(self.request(server, "GET", "/../../main.py")[0], 404)

    def test_real_source_javascript_and_panel_security_contract(self):
        index = (UPSTREAM / "app/web/remote/index.html").read_text()
        patched = ui.patch_index(index)
        self.assertEqual(ui.patch_index(patched), patched)
        self.assertLess(patched.index('data-settings-page-content="global"'), patched.index('id="naiaAndroidToolsSettings"'))
        self.assertLess(patched.index('id="naiaAndroidToolsSettings"'), patched.index('data-settings-page-content="extension"'))
        panel = (ROOT / "android_tools_panel.html").read_text()
        injected = ui._injection("http://127.0.0.1:32101/#token=test-capability")
        self.assertIn("event.source!==frame.contentWindow", injected)
        self.assertIn("event.origin!==panelOrigin", injected)
        self.assertIn("window.dispatchEvent(new CustomEvent('naia-artist-index-ready'", injected)
        self.assertIn("history.replaceState", panel)
        self.assertIn("event.source!==window.parent", panel)
        self.assertIn("event.origin!==parentOrigin", panel)
        self.assertIn("setInterval(poll,3000)", panel)
        self.assertNotIn("document.hidden", panel)
        self.assertNotIn("localStorage", panel)
        with tempfile.TemporaryDirectory() as directory:
            for i, text in enumerate((panel, injected)):
                for j, script in enumerate(re.findall(r"<script>(.*?)</script>", text, re.S)):
                    path = Path(directory) / f"check{i}_{j}.mjs"; path.write_text(script)
                    subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)

    def test_chain_preserves_previous_response_and_cleanup(self):
        from fastapi.responses import FileResponse, HTMLResponse
        web_root = UPSTREAM / "app/web/remote"
        routes = types.ModuleType("app.backend.server.web_shell_routes")
        calls = []
        def previous_file(path, media_type):
            calls.append(("file", path))
            return HTMLResponse(Path(path).read_text().replace("</body>", "<!-- earlier-extension -->\n</body>"))
        def previous_asset(root, asset, *, default_media_type):
            calls.append(("asset", asset)); return FileResponse(root / asset, media_type=default_media_type)
        routes._web_file, routes._web_asset = previous_file, previous_asset
        app_web = types.ModuleType("app.web"); app_web.resolve_remote_web_dir = lambda root: web_root
        folder = types.SimpleNamespace(patch_asset=lambda path, text: text+"\n// folder-adapter")
        memory = types.SimpleNamespace(patch_artist_asset=lambda text: text+"\n// memory-adapter")
        with patch.dict(sys.modules, {"app.web": app_web, "app.backend.server.web_shell_routes": routes}):
            handle = ui.install_ui(Controller(), folder, memory)
            try:
                response = routes._web_file(web_root / "index.html", "text/html")
                self.assertIn(b"earlier-extension", response.body)
                self.assertIn(b"naiaAndroidToolsSettings", response.body)
                self.assertNotIn("etag", response.headers)
                response = routes._web_asset(web_root / "js", "features/artistThumbTab.mjs", default_media_type="text/javascript")
                self.assertIn(b"folder-adapter", response.body); self.assertIn(b"memory-adapter", response.body)
                later = lambda *args, **kwargs: None
                routes._web_file = later
            finally:
                handle()
            self.assertIs(routes._web_file, later)
            self.assertIs(routes._web_asset, previous_asset)
        self.assertGreaterEqual(len(calls), 5)


if __name__ == "__main__":
    unittest.main()
