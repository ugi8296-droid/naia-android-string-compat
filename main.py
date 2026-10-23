"""Folder-list selection for the existing NAIA app; no native SAF claim."""
from __future__ import annotations

import atexit
import hashlib
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
        if fn.__module__ == _PACKAGE + ".naia_storage_ui":
            server = values.get("server")
            if type(server).__module__ == _PACKAGE + ".naia_storage_ui":
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
    _retire_previous()
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
    sys.modules[_PACKAGE]._controller = controller
    atexit.register(controller.close)
    ctx.log("저장 폴더 목록 선택이 준비되었습니다. 화면을 새로고침하세요.")
