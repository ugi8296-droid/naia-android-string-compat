"""NAIA extension entry point. No authentication or inference on installation."""
from __future__ import annotations

import atexit
import importlib
import importlib.util
from pathlib import Path
import re
import sys
import threading
import types

_PACKAGE = "_naia_chatgpt_connect_v1"
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
        raise RuntimeError("다른 ChatGPT 연결 확장이 이미 실행 중입니다. 앱을 다시 시작하세요.")
    return importlib.import_module(f"{_PACKAGE}.{name}")


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


def register(ctx):
    global _controller
    if _controller is not None:
        _controller.close()
        _controller = None
    auth_type = _module("naia_siwc_auth").AuthManager
    client_type = _module("naia_siwc_responses").ResponsesClient
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
             "help": "화면의 ChatGPT 연결 버튼에서 로그인하고 모델을 선택하세요. 요청은 ChatGPT 이용 한도를 사용합니다."},
            {"key": "help", "type": "action", "label": "연결 화면 안내"},
        ], title="ChatGPT 구독 연결", on_action=lambda key: ctx.show_toast(
            "앱 화면을 새로고침한 뒤 ChatGPT 연결 버튼을 누르세요. 로그인 → 모델 목록 → 모델 선택 → 사용 켜기 순서입니다."
        ) if key == "help" else None)
    except BaseException:
        controller.close()
        raise
    _controller = controller
    atexit.register(controller.close)
    ctx.log("ChatGPT 연결 기능이 준비되었습니다. 화면을 새로고침하세요. 로그인과 AI 호출은 아직 실행하지 않았습니다.")
