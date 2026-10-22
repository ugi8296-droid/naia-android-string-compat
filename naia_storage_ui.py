"""Reversible, version-checked UI for app-accessible filesystem directories.

This is not Android's Storage Access Framework and grants no storage permission.
"""
from __future__ import annotations

import hmac
import importlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _replace(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("저장 폴더 화면 버전이 달라 확장을 적용하지 않았습니다.")
    return source.replace(old, new, 1)


def patch_feature(source, panel_url=None):
    """Keep all existing save options and the host's native picker."""
    source = _replace(source, "  let lastState = null;", """  let lastState = null;
  let storagePickerPending = false;
  async function openAccessibleFolders() {
    if (storagePickerPending || !lastState?.control_allowed || !lastState?.browse_allowed) return;
    storagePickerPending = true;
    try {
      const state = await globalThis.__naiaOpenStoragePicker?.();
      if (state) {
        render(state);
        if (typeof globalThis.requestModuleState === 'function') globalThis.requestModuleState('save_directory');
        if (showToast) showToast('선택한 폴더를 저장 위치로 적용했습니다.', 'success');
      }
    } catch (_) {
      if (showToast) showToast('폴더 선택 화면을 열지 못했습니다. 화면을 새로고침해 주세요.', 'error');
    } finally { storagePickerPending = false; }
  }""")
    source = _replace(source, '        <label class="mod-field">\n          <span class="mod-field-label">Base Save Path</span>', """        <div class="mod-field">
          <button type="button" class="mod-btn-secondary mod-btn-secondary--accent" id="naiaAccessibleFolderPick"
                  ${controlAllowed && browseAllowed ? '' : 'disabled'}>📁 폴더 목록에서 선택</button>
          <div class="mod-debug-empty">앱이 접근할 수 있는 폴더에서 선택합니다. Android 시스템 권한 선택기가 아닙니다.</div>
          ${canPick ? `<button type="button" class="mod-btn-secondary" onclick="pickSaveDirectory()">기존 시스템 폴더 선택…</button>` : ''}
        </div>
        <details class="mod-field"><summary>고급: 경로 직접 입력</summary>
        <label class="mod-field">
          <span class="mod-field-label">Base Save Path</span>""")
    source = _replace(source, '          ${browseNotice}\n        </label>\n        <label class="mod-checkbox-item">', '          ${browseNotice}\n        </label>\n        </details>\n        <label class="mod-checkbox-item">')
    source = _replace(source, '    `;\n  }\n\n  function browse()', """    `;
    document.getElementById('naiaAccessibleFolderPick')?.addEventListener('click', openAccessibleFolders);
  }

  function browse()""")
    source = _replace(source, "    if (lastState) {\n      lastState.base_path = value;", """    if (/^[a-z][a-z0-9+.-]*:/i.test(value) && !/^[a-z]:[\\\\/]/i.test(value)) {
      if (showToast) showToast('Android 문서 URI는 직접 입력할 수 없습니다. 폴더 목록에서 접근 가능한 폴더를 선택해 주세요.', 'error');
      return;
    }
    if (lastState) {
      lastState.base_path = value;""")
    source = _replace(source, "    if (!folder) return;   // 사용자 취소", """    if (!folder) return;   // 사용자 취소
    if (typeof folder !== 'string' || (/^[a-z][a-z0-9+.-]*:/i.test(folder) && !/^[a-z]:[\\\\/]/i.test(folder))) {
      if (showToast) showToast('이 저장 기능은 파일 경로만 지원합니다. Android 문서 URI 저장에는 APK의 권한 연동이 필요합니다.', 'error');
      return;
    }""")
    if panel_url:
        source += "\n" + _injection(panel_url).removeprefix("<script>").removesuffix("</script>")
    return source


class PanelServer:
    """Private loopback API; capability stays in a fragment, never request URLs."""
    def __init__(self, controller):
        self.controller = controller
        self.token = secrets.token_urlsafe(32)
        self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data, content_type="application/json"):
                body = (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)).encode("utf-8")
                self.send_response(code)
                for key, value in {
                    "Content-Type": content_type + "; charset=utf-8", "Content-Length": str(len(body)),
                    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors http://127.0.0.1:* http://localhost:*",
                }.items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def valid(self, mutation=False):
                if self.headers.get("Host") != owner.authority:
                    self.reply(403, {"error": {"message": "요청 주소가 올바르지 않습니다."}})
                    return False
                origin = self.headers.get("Origin")
                if (mutation and origin != owner.origin) or (origin is not None and origin != owner.origin):
                    self.reply(403, {"error": {"message": "다른 화면에서는 접근할 수 없습니다."}})
                    return False
                token = self.headers.get("X-NAIA-Storage", "").encode("utf-8")
                if owner.closed or not hmac.compare_digest(token, owner.token.encode("ascii")):
                    self.reply(403, {"error": {"message": "NAIA 화면을 새로고침해 주세요."}})
                    return False
                return True

            def do_OPTIONS(self):
                self.reply(403, {"error": {"message": "교차 출처 요청은 허용되지 않습니다."}})

            def do_GET(self):
                if self.path == "/" and self.headers.get("Host") == owner.authority and not owner.closed:
                    self.reply(200, Path(__file__).with_name("storage_panel.html").read_text(encoding="utf-8"), "text/html")
                elif self.valid():
                    if self.path != "/state":
                        self.reply(404, {"error": {"message": "없는 요청입니다."}})
                        return
                    self.invoke(owner.controller.state)

            def invoke(self, method, *args):
                try:
                    result = method(*args)
                    self.reply(200 if result.get("ok") else 400, result)
                except Exception:
                    self.reply(500, {"ok": False, "error": {"message": "폴더에 접근하지 못했습니다. 앱의 저장 권한과 폴더 상태를 확인해 주세요."}})

            def do_POST(self):
                if not self.valid(mutation=True):
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                    self.reply(415, {"error": {"message": "JSON 요청이 필요합니다."}})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < size <= 8192:
                        raise ValueError()
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict) or not isinstance(data.get("root_id"), str) or not isinstance(data.get("relative", ""), str):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    self.reply(400, {"error": {"message": "올바르지 않은 폴더 요청입니다."}})
                    return
                args = (data["root_id"], data.get("relative", ""))
                if self.path == "/list":
                    self.invoke(owner.controller.list_directories, *args)
                elif self.path == "/apply":
                    self.invoke(owner.controller.apply_selection, *args)
                elif self.path == "/mkdir" and isinstance(data.get("name"), str):
                    self.invoke(owner.controller.create_directory, *args, data["name"])
                else:
                    self.reply(400, {"error": {"message": "없는 작업이거나 값이 올바르지 않습니다."}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.authority = "127.0.0.1:" + str(self.server.server_port)
        self.origin = "http://" + self.authority
        self.panel_url = self.origin + "/#" + self.token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="naia-storage-picker")
        self.thread.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _injection(panel_url):
    return """<script>(()=>{
if(globalThis.__naiaOpenStoragePicker)return;
let current=null;
globalThis.__naiaOpenStoragePicker=()=>{
 if(current)return Promise.resolve(null);
 return new Promise(resolve=>{
  const id=globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const url=new URL(PANEL_URL), origin=url.origin;
  url.hash=new URLSearchParams({token:url.hash.slice(1),request:id,parent:location.origin}).toString();
  const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','저장 폴더 선택');
  dialog.style.cssText='padding:0;border:1px solid #567477;border-radius:16px;width:min(640px,96vw);max-height:94vh;background:#111d22;color:#edf5f7;z-index:2147483647';
  const frame=document.createElement('iframe');frame.title='앱이 접근할 수 있는 폴더';frame.referrerPolicy='no-referrer';
  frame.style.cssText='width:100%;height:min(780px,88vh);border:0;display:block';frame.src=url.href;
  let done=false,busy=false;
  const finish=state=>{if(done)return;done=true;window.removeEventListener('message',receive);dialog.close();dialog.remove();current=null;resolve(state);};
  const receive=e=>{
   if(e.source!==frame.contentWindow||e.origin!==origin||e.data?.request!==id)return;
   if(e.data.type==='naia-storage-commit'){busy=true;frame.contentWindow.postMessage({type:'naia-storage-commit-ok',request:id},origin);}
   if(e.data.type==='naia-storage-idle')busy=false;
   if(e.data.type==='naia-storage-cancel'&&!busy)finish(null);
   if(e.data.type==='naia-storage-applied'&&busy&&e.data.state?.module_id==='save_directory')finish(e.data.state);
  };
  window.addEventListener('message',receive);
  dialog.addEventListener('cancel',e=>{e.preventDefault();if(!busy)finish(null);});
  current=dialog;dialog.append(frame);document.body.append(dialog);dialog.showModal();
 });
};
})();</script>""".replace("PANEL_URL", json.dumps(panel_url))


def _response_text(response, fallback):
    if getattr(response, "status_code", 200) != 200:
        raise RuntimeError("저장 폴더 화면 응답을 확인하지 못했습니다.")
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8")
    if isinstance(body, str):
        return body
    path = getattr(response, "path", None)
    if path is not None and Path(path).resolve() == fallback.resolve():
        return fallback.read_text(encoding="utf-8")
    raise RuntimeError("다른 확장의 화면 응답과 호환되지 않아 적용하지 않았습니다.")


def install_ui(controller):
    """Preflight actual host assets, then wrap their existing serving chain."""
    module = importlib.import_module("app.backend.server.web_shell_routes")
    from fastapi.responses import Response
    from app.web import resolve_remote_web_dir

    previous_asset = module._web_asset
    try:
        root = Path(resolve_remote_web_dir(controller.ctx._app_context.repo_root)).resolve()
        asset_root = root / "js"
        asset = "features/saveDirectoryPanel.mjs"
        patch_feature(_response_text(previous_asset(asset_root, asset, default_media_type="text/javascript"), asset_root / asset))
    except (AttributeError, OSError, UnicodeError) as exc:
        raise RuntimeError("실제 NAIA 화면 파일을 확인하지 못해 저장 폴더 확장을 적용하지 않았습니다.") from exc
    server = PanelServer(controller)
    active = True

    def web_asset(root, asset_path, *, default_media_type="application/octet-stream"):
        previous = previous_asset(root, asset_path, default_media_type=default_media_type)
        if active and Path(root).resolve() == asset_root and asset_path == asset:
            return Response(patch_feature(_response_text(previous, asset_root / asset), server.panel_url), media_type="text/javascript", headers={"Cache-Control": "no-store"})
        return previous

    module._web_asset = web_asset

    def cleanup():
        nonlocal active
        if not active:
            return
        active = False
        if module._web_asset is web_asset:
            module._web_asset = previous_asset
        server.close()

    cleanup.server = server
    cleanup.panel_url = server.panel_url
    return cleanup
