"""Folder-list selection for the existing NAIA app; no native SAF claim."""
from __future__ import annotations

import atexit
import importlib
import importlib.util
from pathlib import Path
import sys
import types

_PACKAGE = "_naia_storage_picker_v1"
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
        raise RuntimeError("다른 저장 폴더 확장이 실행 중입니다. 앱을 다시 시작하세요.")
    return importlib.import_module(f"{_PACKAGE}.{name}")


class Controller:
    def __init__(self, ctx, service):
        self.ctx = ctx
        self._service = service
        self.service = self  # UI accesses only the activation-gated interface.
        self._closed = False
        self._cleanup = None

    def _call(self, method, *args, **kwargs):
        record = self.ctx._record
        if self._closed or not record.is_active:
            return {"ok": False, "error": {"code": "extension_disabled",
                    "message": "Settings에서 저장 폴더 선택 확장과 작동 스위치를 켜 주세요."}}
        return getattr(self._service, method)(*args, **kwargs)

    def state(self):
        return self._call("state")

    def list_directories(self, root_id, relative=""):
        return self._call("list_directories", root_id, relative)

    def apply_selection(self, root_id, relative=""):
        return self._call("apply_selection", root_id, relative)

    def create_directory(self, root_id, relative="", name=""):
        return self._call("create_directory", root_id, relative, name)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._cleanup:
            self._cleanup()


def register(ctx):
    global _controller
    if _controller:
        _controller.close()
        _controller = None
    service = _module("naia_storage_service").StorageDirectoryService(ctx._app_context)
    controller = Controller(ctx, service)
    try:
        handle = _module("naia_storage_ui").install_ui(controller)
        controller._cleanup = handle if callable(handle) else handle.close
        ctx.register_panel([
            {"key": "help", "type": "action", "label": "저장 폴더 선택 안내",
             "help": "저장경로 설정에서 폴더 목록으로 선택합니다. 새 Android 접근 권한을 부여하는 기능은 아닙니다."},
        ], title="저장 폴더 선택", on_action=lambda key: ctx.show_toast(
            "화면을 새로고침한 뒤 저장경로 톱니바퀴 → 폴더 목록에서 선택을 누르세요."
        ) if key == "help" else None)
    except BaseException:
        controller.close()
        raise
    _controller = controller
    atexit.register(controller.close)
    ctx.log("저장 폴더 목록 선택이 준비되었습니다. 화면을 새로고침하세요.")
