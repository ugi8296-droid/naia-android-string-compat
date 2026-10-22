"""NAIA v2.0.48 Android tools. Credentials stay in private app storage."""
from __future__ import annotations
import atexit
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import types

RELAY_URL = "https://naia-tablet-link.ugi8296.chatgpt.site"
DEFAULTS = {"low_memory": True, "cache_mb": 32, "plugin_enabled": False,
            "allow_read": True, "allow_edit": False, "allow_generate": False}
_PACKAGE = "_naia_android_tools_v1"
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
        raise RuntimeError("Android 도구의 이전 실행부가 남아 있습니다. 앱을 다시 시작하세요.")
    return importlib.import_module(f"{_PACKAGE}.{name}")

def _private_directory(ctx):
    config = getattr(getattr(ctx._app_context, "runtime_paths", None), "config_dir", None)
    if config is None:
        raise RuntimeError("앱 전용 설정 폴더를 확인할 수 없습니다.")
    directory = (Path(config) / "android-tools").resolve()
    if hasattr(sys, "getandroidapilevel") and not re.match(
        r"^/data/(?:user(?:_de)?/\d+|data)/[^/]+/(?:files|no_backup)/", str(directory)):
        raise RuntimeError("연결 정보를 저장할 Android 앱 전용 폴더가 필요합니다.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory

def _pairing(data):
    if not isinstance(data, dict) or not isinstance(data.get("relay_url"), str) or data["relay_url"].rstrip("/") != RELAY_URL:
        raise ValueError("NAIA 연결 페이지에서 받은 연결 파일을 선택하세요.")
    result = {"relay_url": RELAY_URL}
    for key in ("relay_access_token", "device_id", "device_token"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 8192 or any(c in value for c in "\r\n\0"):
            raise ValueError("연결 파일 형식이 올바르지 않습니다.")
        result[key] = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", result["device_id"]):
        raise ValueError("연결 파일의 기기 정보가 올바르지 않습니다.")
    return result

class Controller:
    def __init__(self, ctx, state_root, folder_module):
        self.ctx, self.state_root, self.folder_module = ctx, Path(state_root), folder_module
        self.pair_path = self.state_root / "pairing.json"
        self._lock, self._closed, self._cleanup = threading.RLock(), False, []
        self.memory, self.bridge, self._pair = None, None, None
        try:
            if self.pair_path.stat().st_size <= 32768:
                self._pair = _pairing(json.loads(self.pair_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass

    def available(self):
        r = self.ctx._record
        return bool(not self._closed and r.status == "loaded" and r.enabled and not r.blocked)

    def active(self):
        return bool(self.available() and self.ctx._record.armed)

    def require_available(self):
        if not self.available():
            raise ValueError("Settings → Extension에서 Android 도구를 활성화하세요.")

    def settings(self):
        raw = self.ctx.load_settings(dict(DEFAULTS))
        result = {k: raw.get(k, v) is True for k, v in DEFAULTS.items() if k != "cache_mb"}
        result["cache_mb"] = raw.get("cache_mb") if type(raw.get("cache_mb")) is int and raw["cache_mb"] in (16, 32, 64) else 32
        return result

    def memory_settings(self):
        settings = self.settings()
        return {"low_memory": settings["low_memory"] and self.active(), "cache_mb": settings["cache_mb"]}

    def bridge_settings(self):
        with self._lock:
            settings = self.settings()
            return {**(self._pair or {}), "enabled": settings["plugin_enabled"] and bool(self._pair) and self.active(),
                    "permissions": {k: settings["allow_" + k] for k in ("read", "edit", "generate")}}

    def status(self):
        memory = self.memory.refresh() if self.memory else {}
        with self._lock:
            return {"settings": self.settings(), "memory": memory,
                "bridge": self.bridge.status() if self.bridge else {"state": "stopped"},
                "paired": bool(self._pair), "extension_active": self.available(),
                "armed": bool(self.ctx._record.armed), "relay_url": RELAY_URL}

    def _save(self, settings):
        if not self.ctx.save_settings(settings):
            raise ValueError("설정을 저장하지 못했습니다. 다시 시도하세요.")

    def update_settings(self, changes):
        self.require_available()
        if not isinstance(changes, dict) or not changes or set(changes) - DEFAULTS.keys():
            raise ValueError("설정 형식이 올바르지 않습니다.")
        for key, value in changes.items():
            if key == "cache_mb":
                if type(value) is not int or value not in (16, 32, 64):
                    raise ValueError("캐시 크기는 16, 32, 64MB 중 선택하세요.")
            elif type(value) is not bool:
                raise ValueError("설정은 켜기 또는 끄기여야 합니다.")
        with self._lock:
            new = {**self.settings(), **changes}
            if new["plugin_enabled"] and not self._pair:
                raise ValueError("먼저 연결 파일을 불러오세요.")
            self._save(new)
        return self.status()

    def import_pairing(self, data):
        self.require_available()
        pair = _pairing(data)
        with self._lock:
            # Persist revocation first: a failed import never inherits old writes.
            disabled = {**self.settings(), "plugin_enabled": False, "allow_read": True,
                        "allow_edit": False, "allow_generate": False}
            self._save(disabled)
            temporary = self.pair_path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(pair, file, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                temporary.chmod(0o600)
                os.replace(temporary, self.pair_path)
            finally:
                temporary.unlink(missing_ok=True)
            self._pair = pair
            self._save({**disabled, "plugin_enabled": True})
        return self.status()

    def disconnect(self):
        self.require_available()
        with self._lock:
            self._save({**self.settings(), "plugin_enabled": False, "allow_edit": False, "allow_generate": False})
            self._pair = None
            self.pair_path.unlink(missing_ok=True)
        return self.status()

    def open_folder(self, target):
        self.require_available()
        if not self.active():
            return {"opened": False, "reason": "extension_inactive"}
        app = self.ctx._app_context
        if target == "save":
            path = app._save_service().current_save_directory()
        elif target == "artist":
            from app.backend.server.artist_thumbnail_routes import artist_thumbnail_service
            path = artist_thumbnail_service(app).mode_data_root
        else:
            raise ValueError("열 수 있는 폴더가 아닙니다.")
        return self.folder_module.open_native_folder(path)

    def open_relay(self):
        self.require_available()
        opened = False
        if sys.platform == "android" or hasattr(sys, "getandroidapilevel"):
            try:
                r = subprocess.run(["/system/bin/am", "start", "-a", "android.intent.action.VIEW",
                    "-c", "android.intent.category.BROWSABLE", "-d", RELAY_URL],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False)
                opened = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                pass
        return {"opened": opened, "url": RELAY_URL}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self.bridge:
            self.bridge.close()
        for cleanup in reversed(self._cleanup):
            try:
                cleanup()
            except Exception:
                pass
        if self.memory:
            self.memory.close()

def register(ctx):
    global _controller
    if _controller:
        _controller.close()
        _controller = None
    folder, memory, bridge, ui = [_module(n) for n in
        ("naia_folder_open", "naia_artist_memory", "naia_device_bridge", "naia_tools_ui")]
    controller = Controller(ctx, _private_directory(ctx), folder)
    try:
        controller.memory = memory.install(controller.memory_settings, state_root=controller.state_root)
        existing = getattr(ctx._app_context, "artist_thumbnail_service", None)
        if existing is not None:
            controller.memory.attach(existing)
        controller.bridge = bridge.DeviceBridge(ctx, controller.bridge_settings,
            state_root=controller.state_root, allowed_origins={RELAY_URL})
        handle = ui.install_ui(controller, folder, memory)
        controller._cleanup.append(handle if callable(handle) else handle.close)
        ctx.register_panel([{"key": "help", "type": "action", "label": "Android 도구 설정 안내",
            "help": "Settings → Global → Android 도구 · 플러그인에서 설정합니다."}],
            title="Android 도구 0.1.0", on_action=lambda key: ctx.show_toast(
                "화면 새로고침 후 Settings → Global → Android 도구 · 플러그인을 여세요.") if key == "help" else None)
        controller.bridge.start()
    except BaseException:
        controller.close()
        raise
    _controller = controller
    atexit.register(controller.close)
    ctx.log("Android 도구가 준비되었습니다. 화면을 새로고침하세요. 플러그인 연결은 설정에서 시작합니다.")
