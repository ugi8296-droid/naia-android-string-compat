"""NAIA extension entry point. No authentication or inference on installation."""
from __future__ import annotations

import atexit
import hashlib
import importlib
import importlib.util
from pathlib import Path
import re
import sys
import threading
import types

_PACKAGE = "_naia_chatgpt_connect_v2"
_controller = None


def _module(name):
    directory = Path(__file__).resolve().parent
    package = sys.modules.get(_PACKAGE)
    if package is None:
        package = types.ModuleType(_PACKAGE)
        package.__path__ = [str(directory)]
        package.__package__ = _PACKAGE
        package.__spec__ = importlib.util.spec_from_loader(_PACKAGE, loader=None, is_package=True)
        sys.modules[_PACKAGE] = package
    elif Path(package.__path__[0]).resolve() != directory:
        raise RuntimeError("다른 위치의 같은 확장이 실행 중이어서 적용을 중단했습니다.")
    path = directory / (name + ".py")
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    fullname = _PACKAGE + "." + name
    previous = sys.modules.get(fullname)
    if previous is not None and getattr(previous, "__naia_source_digest", None) == digest:
        return previous
    spec = importlib.util.spec_from_file_location(fullname, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = module
    try:
        # Execute the verified source, without stale timestamp-based bytecode.
        exec(compile(source, str(path), "exec"), module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(fullname, None)
        else:
            sys.modules[fullname] = previous
        raise
    module.__naia_source_digest = digest
    return module


def _retire_previous():
    """Recover controllers even after the host replaced the entry module.

    Only our package's serving wrappers and our exact installation directory
    are considered. Closing preserves stored credentials and saved settings.
    """
    directory = Path(__file__).resolve().parent
    package = sys.modules.get(_PACKAGE)
    if package is not None and Path(package.__path__[0]).resolve() != directory:
        raise RuntimeError("다른 위치의 같은 확장이 실행 중입니다.")
    controllers = [_controller, getattr(package, "_controller", None)]
    shell = sys.modules.get("app.backend.server.web_shell_routes")
    pending = [getattr(shell, name, None) for name in ("_web_file", "_web_asset")]
    seen = set()
    while pending and len(seen) < 128:
        fn = pending.pop()
        if not isinstance(fn, types.FunctionType) or id(fn) in seen:
            continue
        seen.add(id(fn))
        values = {}
        for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
            try:
                values[name] = cell.cell_contents
            except ValueError:
                pass
        pending.extend(values.get(name) for name in ("previous_file", "previous_asset"))
        if fn.__module__ == _PACKAGE + ".naia_chatgpt_ui":
            server = values.get("server")
            if type(server).__module__ == _PACKAGE + ".naia_chatgpt_ui":
                controllers.append(getattr(server, "controller", None))
    closed = set()
    for controller in controllers:
        if controller is None or id(controller) in closed:
            continue
        context = getattr(controller, "ctx", None)
        if context is None or Path(context.ext_dir).resolve() != directory:
            raise RuntimeError("이전 확장의 설치 경로가 달라 안전하게 중지하지 못했습니다.")
        controller.close()
        closed.add(id(controller))
    if package is not None:
        package._controller = None
    bridge = sys.modules.get(_PACKAGE + ".naia_chatgpt_bridge")
    cleanup = getattr(bridge, "_ACTIVE_CLEANUP", None)
    if callable(cleanup):
        cleanup()


def _credential_directory(ctx):
    paths = getattr(getattr(ctx, "_app_context", None), "runtime_paths", None)
    config = getattr(paths, "config_dir", None)
    if config is None:
        raise RuntimeError("앱의 비공개 설정 폴더를 확인할 수 없습니다.")
    directory = (Path(config) / "chatgpt-auth").resolve()
    if hasattr(sys, "getandroidapilevel"):
        # External/shared storage does not protect refresh tokens, even with chmod.
        if not re.match(r"^/data/(?:user(?:_de)?/\d+|data)/[^/]+/(?:files|no_backup)/", str(directory)):
            raise RuntimeError("ChatGPT 연결 정보를 저장할 Android 앱 전용 폴더가 필요합니다.")
    return directory


class Controller:
    def __init__(self, ctx, auth, client):
        self.ctx = ctx
        self.auth = auth
        self.client = client
        self._lock = threading.RLock()
        self._models = []
        self._cleanup = []
        self._closed = False

    def _available(self):
        record = self.ctx._record
        return bool(record.status == "loaded" and record.enabled and not record.blocked and not self._closed)

    def _require_available(self):
        if not self._available():
            raise RuntimeError("설정에서 ChatGPT 연결 확장을 활성화하세요.")

    def settings(self):
        raw = self.ctx.load_settings({"enabled": False, "model": ""})
        return {
            "enabled": bool(raw.get("enabled") is True and self._available() and self.ctx._record.armed),
            "model": str(raw.get("model") or "")[:200],
        }

    def status(self):
        raw = self.ctx.load_settings({"enabled": False, "model": ""})
        with self._lock:
            return {
                **self.settings(),
                "configured_enabled": raw.get("enabled") is True,
                "extension_active": self._available(),
                "armed": bool(self.ctx._record.armed),
                "auth": self.auth.status(),
                "models": [dict(item) for item in self._models],
                "error": "" if self._available() else "ChatGPT 연결 확장이 꺼져 있습니다.",
            }

    def begin_login(self):
        self._require_available()
        state = self.auth.status()
        return self.auth.begin_login(consent=bool(state.get("connected") and not state.get("plan_enabled")))

    def list_models(self):
        self._require_available()
        models = self.client.list_models()
        with self._lock:
            self._models = [{"slug": item["slug"], "display_name": item["display_name"]} for item in models]
            return [dict(item) for item in self._models]

    def _save(self, **changes):
        with self._lock:
            raw = self.ctx.load_settings({"enabled": False, "model": ""})
            # Keep only ordinary UI preferences. Never put login state in settings.json.
            settings = {"enabled": raw.get("enabled") is True, "model": str(raw.get("model") or "")[:200]}
            settings.update(changes)
            if not self.ctx.save_settings(settings):
                raise RuntimeError("ChatGPT 설정을 저장하지 못했습니다.")

    def select_model(self, slug):
        self._require_available()
        with self._lock:
            if not isinstance(slug, str) or slug not in {item["slug"] for item in self._models}:
                raise ValueError("모델 목록을 새로고침한 뒤 사용할 모델을 선택하세요.")
            self._save(model=slug)
        return self.status()

    def set_enabled(self, enabled):
        self._require_available()
        if type(enabled) is not bool:
            raise ValueError("연결 사용 설정은 켜기 또는 끄기여야 합니다.")
        if enabled:
            if not self.ctx._record.armed:
                raise ValueError("확장 패널의 Activate This Script 스위치를 켜 주세요.")
            if not self.settings()["model"]:
                raise ValueError("로그인 후 사용할 모델을 먼저 선택하세요.")
            auth = self.auth.status()
            if not auth.get("connected") or not auth.get("plan_enabled"):
                raise ValueError("ChatGPT에 로그인하고 구독 사용 권한을 승인하세요.")
        self._save(enabled=enabled)
        return self.status()

    def disconnect(self):
        self._require_available()
        self._save(enabled=False, model="")
        with self._lock:
            self._models = []
        return self.auth.disconnect()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for cleanup in reversed(self._cleanup):
            try:
                cleanup()
            except Exception:
                pass
        self.auth.close()


def _close_disabled_previous_version(ctx):
    """Retire only the explicitly disabled/blocked v1, without changing flags."""
    old_id = "naia_chatgpt_connect_v1"
    package_name = "_" + old_id
    previous = sys.modules.get("naia_ext_" + old_id)
    package = sys.modules.get(package_name)
    controllers = [getattr(previous, "_controller", None), getattr(package, "_controller", None)]
    shell = sys.modules.get("app.backend.server.web_shell_routes")
    pending = [getattr(shell, name, None) for name in ("_web_file", "_web_asset")]
    seen = set()
    while pending and len(seen) < 128:
        fn = pending.pop()
        if not isinstance(fn, types.FunctionType) or id(fn) in seen:
            continue
        seen.add(id(fn))
        values = {}
        for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
            try:
                values[name] = cell.cell_contents
            except ValueError:
                pass
        pending.extend(values.get(name) for name in ("previous_file", "previous_asset"))
        if fn.__module__ == package_name + ".naia_chatgpt_ui":
            server = values.get("server")
            if type(server).__module__ == package_name + ".naia_chatgpt_ui":
                controllers.append(getattr(server, "controller", None))
    controllers = list({id(c): c for c in controllers if c is not None}.values())
    bridge = sys.modules.get(package_name + ".naia_chatgpt_bridge")
    cleanup = getattr(bridge, "_ACTIVE_CLEANUP", None)
    if not controllers and not callable(cleanup):
        return
    manager = getattr(ctx._app_context, "extension_manager", None)
    record = next((r for r in getattr(manager, "records", ()) if r.ext_id == old_id), None)
    if record is None and controllers:
        record = getattr(getattr(controllers[0], "ctx", None), "_record", None)
    if (getattr(record, "ext_id", None) != old_id or not (
            getattr(record, "enabled", None) is False or getattr(record, "blocked", None) is True)):
        raise RuntimeError("Settings → Extension에서 이전 ChatGPT 구독 연결 확장을 먼저 끈 뒤 새 버전을 활성화하세요.")
    directory = Path(record.directory).resolve()
    if package is not None and Path(package.__path__[0]).resolve() != directory:
        raise RuntimeError("이전 ChatGPT 연결 확장의 설치 경로를 확인하지 못했습니다.")
    for controller in controllers:
        context = getattr(controller, "ctx", None)
        if (getattr(getattr(context, "_record", None), "ext_id", None) != old_id
                or Path(context.ext_dir).resolve() != directory):
            raise RuntimeError("이전 ChatGPT 연결 확장의 실행 정보를 확인하지 못했습니다.")
    for controller in controllers:
        controller.close()
    cleanup = getattr(bridge, "_ACTIVE_CLEANUP", None)
    if callable(cleanup):
        cleanup()
    if package is not None:
        package._controller = None


def register(ctx):
    global _controller
    _close_disabled_previous_version(ctx)
    _retire_previous()
    _controller = None
    auth_type = _module("naia_siwc_auth").AuthManager
    client_type = _module("naia_siwc_responses").ResponsesClient
    _module("naia_chatgpt_schema")
    bridge = _module("naia_chatgpt_bridge")
    ui = _module("naia_chatgpt_ui")
    auth = auth_type(_credential_directory(ctx))
    controller = Controller(ctx, auth, client_type(auth))
    try:
        controller._cleanup.append(bridge.install_bridge(auth, controller.client, controller.settings))
        ui_handle = ui.install_ui(controller)
        controller._cleanup.append(ui_handle if callable(ui_handle) else ui_handle.close)
        ctx.register_panel([
            {"key": "enabled", "type": "bool", "label": "AI Assist · Boost에 ChatGPT 사용", "default": False,
             "help": "Settings → Global → ChatGPT 연결에서 로그인하고 모델을 선택하세요. 요청은 ChatGPT 이용 한도를 사용합니다."},
            {"key": "help", "type": "action", "label": "연결 화면 안내"},
        ], title="ChatGPT 구독 연결 0.1.2", on_action=lambda key: ctx.show_toast(
            "Settings → Global → ChatGPT 연결을 누르세요. 로그인 → 모델 목록 → 모델 선택 → 사용 켜기 순서입니다."
        ) if key == "help" else None)
    except BaseException:
        controller.close()
        raise
    _controller = controller
    sys.modules[_PACKAGE]._controller = controller
    atexit.register(controller.close)
    ctx.log("ChatGPT 연결 기능이 준비되었습니다. 화면을 새로고침하세요. 로그인과 AI 호출은 아직 실행하지 않았습니다.")
