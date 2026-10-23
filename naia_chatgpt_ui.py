"""Version-bound, reversible Remote Web bridge; never edits upstream files.

The private web_shell_routes serving seams are deliberately limited to index.html
and three known feature modules. Unknown source revisions fail closed. A separate
loopback server keeps credentials out of extension settings and websocket state.
"""
from __future__ import annotations

import hmac
import importlib
import json
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def open_system_browser(url: str) -> bool:
    """Android's activity manager opens an external ACTION_VIEW, never a WebView."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.username or parsed.password:
        return False
    if not (sys.platform == "android" or hasattr(sys, "getandroidapilevel")):
        return False
    try:
        result = subprocess.run(
            ["/system/bin/am", "start", "-a", "android.intent.action.VIEW", "-c",
             "android.intent.category.BROWSABLE", "-d", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _replace(source, old, new):
    if source.count(new) == 1:
        return source
    if source.count(old) != 1:
        raise RuntimeError("Unsupported NAIA frontend revision; ChatGPT UI adapter was not applied")
    return source.replace(old, new, 1)


def patch_feature(name: str, source: str) -> str:
    """Cloud readiness is separate from installed local engine/model flags."""
    if name == "llmSetupPanel.mjs":
        source = _replace(source, "    if (!st || !elBody) return;", """    if (!st || !elBody) return;
    if (st.cloud) {
      const label = st.cloud_ready ? `ChatGPT · ${st.model_label || st.model_id}` : 'ChatGPT 연결 필요';
      if (elStatus) elStatus.textContent = label;
      const sub = document.getElementById('setupNavSubAi');
      const dot = document.getElementById('setupDotAi');
      if (sub) sub.textContent = label;
      if (dot) dot.className = `setup-nav-dot ${st.cloud_ready ? 'ok' : 'warn'}`;
      elBody.innerHTML = '<p>ChatGPT 클라우드 · 휴대폰에 AI 모델을 다운로드하지 않습니다.</p><button type="button" class="setup-btn-primary" data-chatgpt-open>ChatGPT 연결 및 모델 선택</button>';
      return;
    }""")
    elif name == "boostV2Panel.mjs":
        source = _replace(source, "    if (!statusEl || !actionsEl) return;", """    if (!statusEl || !actionsEl) return;
    if (st.cloud) {
      statusEl.textContent = st.cloud_ready ? `ChatGPT · ${st.model_label || st.model_id}` : 'ChatGPT 로그인 및 모델 선택이 필요합니다';
      actionsEl.innerHTML = '<button class="mod-btn-secondary" data-chatgpt-open>ChatGPT 설정</button>';
      const caption = mountedRoot.querySelector('.mod-boost-caption');
      if (caption) caption.textContent = 'Assist와 Auto Boost가 같은 ChatGPT 모델을 사용합니다.';
      return;
    }""")
        source = _replace(source, "return !!(st && st.engine_ready && st.model_ready);",
                          "return !!(st && (st.cloud ? st.cloud_ready : st.engine_ready && st.model_ready));")
    elif name == "assistPanel.mjs":
        source = _replace(source, "    return !!status && status.model_ready === false;",
                          "    return !!status && (status.cloud ? !status.cloud_ready : status.model_ready === false);")
        source = _replace(source, "    if (!banner) return;", """    if (!banner) return;
    if (status?.cloud) {
      banner.innerHTML = status.cloud_ready ? '' : '<div class="as-banner-row"><span>ChatGPT 로그인 및 모델 선택이 필요합니다.</span><button type="button" data-chatgpt-open>ChatGPT 연결</button></div>';
      banner.hidden = !!status.cloud_ready;
      if (sendBtn) sendBtn.disabled = busy || !status.cloud_ready;
      paintFollow();
      schedulePoll();
      fit();
      return;
    }""")
        source = _replace(source, "    if (!llm) return '현재 모델 : …';", """    if (!llm) return '현재 모델 : …';
    if (llm.cloud) return llm.cloud_ready ? `ChatGPT : ${llm.model_label || llm.model_id}` : 'ChatGPT : 연결 필요';""")
        source = _replace(source, "  function vramButtonHtml() {", "  function vramButtonHtml() {\n    if (llm?.cloud) return '';")
        source = _replace(source, "  function llmMenuHtml() {", """  function llmMenuHtml() {
    if (llm?.cloud) return '<div class="as-note">ChatGPT 클라우드 모델</div><button type="button" class="as-adv-reset" data-chatgpt-open>연결 및 모델 선택</button>';""")
        source = _replace(source, "      btn.disabled = !llm || !llm.running || !!llm.unload_pending;",
                          "      btn.hidden = !!llm?.cloud;\n      btn.disabled = !llm || !llm.running || !!llm.unload_pending;")
    return source


class PanelServer:
    """Capability-protected loopback control plane. No request URLs are logged."""
    def __init__(self, controller, *, browser_opener=open_system_browser):
        self.controller = controller
        self.browser_opener = browser_opener
        self.token = secrets.token_urlsafe(32)
        self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data, content_type="application/json"):
                body = data.encode("utf-8") if isinstance(data, str) else json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(body)

            def valid(self, *, mutation=False):
                if self.headers.get("Host") != owner.authority:
                    self.reply(403, {"error": "Invalid host"})
                    return False
                origin = self.headers.get("Origin")
                if (mutation and origin != owner.origin) or (origin is not None and origin != owner.origin):
                    self.reply(403, {"error": "Invalid origin"})
                    return False
                supplied = self.headers.get("X-NAIA-ChatGPT", "")
                if owner.closed or not hmac.compare_digest(supplied.encode("utf-8"), owner.token.encode("ascii")):
                    self.reply(403, {"error": "Reload NAIA to reconnect this panel"})
                    return False
                return True

            def do_OPTIONS(self):
                self.reply(403, {"error": "Cross-origin requests are not allowed"})

            def do_GET(self):
                if self.path == "/" and self.headers.get("Host") == owner.authority:
                    self.reply(200, Path(__file__).with_name("chatgpt_panel.html").read_text(encoding="utf-8"), "text/html")
                    return
                if not self.valid():
                    return
                try:
                    if self.path == "/status":
                        self.reply(200, owner.controller.status())
                    elif self.path == "/models":
                        self.reply(200, {"models": owner.controller.list_models()})
                    else:
                        self.reply(404, {"error": "Not found"})
                except Exception:
                    self.reply(502, {"error": "ChatGPT request failed. Check the connection and try again."})

            def do_POST(self):
                if not self.valid(mutation=True):
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    self.reply(415, {"error": "JSON required"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError()
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    self.reply(400, {"error": "Invalid request"})
                    return
                try:
                    if self.path == "/login":
                        attempt = owner.controller.begin_login()
                        url = attempt.get("authorization_url", "")
                        parsed = urlsplit(url)
                        if parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.username or parsed.password:
                            raise ValueError("Invalid authorization destination")
                        opened = owner.browser_opener(url)
                        # URL contains PKCE challenge/state, never an access token or OAuth code.
                        self.reply(200, {"opened": opened, "authorization_url": "" if opened else url})
                    elif self.path == "/disconnect":
                        owner.controller.disconnect()
                        self.reply(200, {"ok": True})
                    elif self.path == "/model" and isinstance(data.get("model"), str):
                        owner.controller.select_model(data["model"])
                        self.reply(200, {"ok": True})
                    elif self.path == "/enabled" and type(data.get("enabled")) is bool:
                        owner.controller.set_enabled(data["enabled"])
                        self.reply(200, {"ok": True})
                    else:
                        self.reply(400, {"error": "Unknown action or invalid value"})
                except Exception:
                    self.reply(502, {"error": "ChatGPT action failed. Check the connection and try again."})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.authority = "127.0.0.1:" + str(self.server.server_port)
        self.origin = "http://" + self.authority
        self.panel_url = self.origin + "/#" + self.token
        self.thread = threading.Thread(target=self.server.serve_forever, name="naia-chatgpt-panel", daemon=True)
        self.thread.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def patch_index(source: str) -> str:
    """Use the host Settings layout; do not cover image actions with a launcher."""
    marker = '<div class="settings-page active" data-settings-page-content="global">'
    return _replace(source, marker, marker + """
          <div class="settings-group" id="naiaChatgptSettings">
            <h3 class="settings-group-title">ChatGPT</h3>
            <div class="settings-toggle-row">
              <button type="button" class="mod-btn-secondary settings-action-btn" data-chatgpt-open>
                ChatGPT 연결
              </button>
              <p class="settings-toggle-desc">계정 연결과 사용할 모델을 설정합니다. Assist와 Auto Boost에 함께 적용됩니다.</p>
            </div>
          </div>""")


def _injection(panel_url):
    return """<script>(()=>{
if(window.__naiaChatgptPanel)return;window.__naiaChatgptPanel=true;
const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','ChatGPT 연결');
dialog.style.cssText='padding:0;border:1px solid #394a4b;border-radius:18px;width:min(560px,94vw);max-height:92vh;background:#0f191c;color:#edf4f3;z-index:2147483647';
const close=document.createElement('button');close.textContent='닫기 ×';close.style.cssText='float:right;margin:10px 16px;padding:8px;border:0;background:transparent;color:inherit;font:inherit';close.onclick=()=>dialog.close();
const frame=document.createElement('iframe');frame.title='ChatGPT 연결 및 모델 선택';frame.referrerPolicy='no-referrer';frame.allow="clipboard-write 'src'";frame.style.cssText='width:100%;height:min(700px,80vh);border:0;display:block';frame.src=PANEL_URL;
dialog.append(close,frame);document.body.append(dialog);
document.addEventListener('click',e=>{if(e.target.closest('[data-chatgpt-open]')){e.preventDefault();dialog.showModal();}});
})();</script>""".replace("PANEL_URL", json.dumps(panel_url))


def install_ui(controller):
    """Return an idempotent cleanup callable with panel_url and server attributes.

    Reload the Remote Web page after enabling/disabling the extension. The seam
    is private and version-bound; patch errors are explicit instead of silently
    leaving Assist gated on a local GGUF installation.
    """
    module = importlib.import_module("app.backend.server.web_shell_routes")
    from fastapi.responses import Response
    from app.web import resolve_remote_web_dir

    # Resolve the same source location as the host and validate every seam before
    # starting a listener or replacing functions. An unknown APK must stay usable.
    previous_file, previous_asset = module._web_file, module._web_asset
    try:
        web_root = Path(resolve_remote_web_dir(controller.ctx._app_context.repo_root)).resolve()
        index_path = web_root / "index.html"
        index_source = _response_text(previous_file(index_path, "text/html"))
        if index_source.count("</body>") != 1:
            raise RuntimeError("Unsupported NAIA index revision")
        index_source = patch_index(index_source)
        asset_root = web_root / "js"
        adapted = {}
        for name in ("llmSetupPanel.mjs", "boostV2Panel.mjs", "assistPanel.mjs"):
            asset = "features/" + name
            patch_feature(name, _response_text(previous_asset(asset_root, asset, default_media_type="text/javascript")))
            adapted[asset] = name
    except (AttributeError, OSError) as exc:
        raise RuntimeError("NAIA frontend could not be validated; ChatGPT UI was not installed") from exc

    server = PanelServer(controller)
    active = True

    def response(previous, text, media_type):
        headers = {k: v for k, v in getattr(previous, "headers", {}).items()
                   if k.lower() not in {"content-length", "content-type", "content-encoding", "etag", "last-modified"}}
        headers["Cache-Control"] = "no-store"
        return Response(text, media_type=media_type, headers=headers,
                        background=getattr(previous, "background", None))

    def web_file(path, media_type):
        previous = previous_file(path, media_type)
        if active and Path(path).resolve() == index_path and media_type == "text/html":
            text = patch_index(_response_text(previous))
            return response(previous, text.replace("</body>", _injection(server.panel_url) + "</body>", 1), "text/html")
        return previous

    def web_asset(root, asset_path, *, default_media_type="application/octet-stream"):
        previous = previous_asset(root, asset_path, default_media_type=default_media_type)
        if active and Path(root).resolve() == asset_root and asset_path in adapted:
            return response(previous, patch_feature(adapted[asset_path], _response_text(previous)), "text/javascript")
        return previous

    module._web_file, module._web_asset = web_file, web_asset

    def cleanup():
        nonlocal active
        if not active:
            return
        active = False
        if module._web_file is web_file:
            module._web_file = previous_file
        if module._web_asset is web_asset:
            module._web_asset = previous_asset
        server.close()

    cleanup.panel_url = server.panel_url
    cleanup.server = server
    return cleanup


def _response_text(response):
    if getattr(response, "status_code", 200) != 200:
        raise RuntimeError("NAIA 화면 파일을 읽지 못했습니다.")
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8")
    if isinstance(body, str):
        return body
    path = getattr(response, "path", None)
    if path is not None and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8")
    raise RuntimeError("이전 확장의 화면 응답을 안전하게 읽을 수 없습니다.")
