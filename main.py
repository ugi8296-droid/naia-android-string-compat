"""One explicit, bounded migration of Android Tools 0.1.0 to 0.2.0.

The normal upstream installer rejects an existing extension ID. This helper
does not import the replacement, grant it trust, or modify any pairing files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import threading
import zipfile

TARGET_ID = "naia_zz_android_tools_v1"
UPDATER_ID = "naia_android_tools_update_020"
FROM_VERSION = "0.1.0"
TO_VERSION = "0.2.0"
ARCHIVE_NAME = "android-tools-0.2.0.zip"
PAYLOAD_SHA256 = "5589ad14905b67dc9fe825556d2f7e230407a0f7b3a20a647ba52f13121674e2"  # build_payload.py pins the reviewed archive in source.
FILES = frozenset({
    "extension.json", "main.py", "naia_artist_memory.py", "naia_device_bridge.py",
    "naia_folder_open.py", "naia_tools_ui.py", "android_tools_panel.html",
    "naia_screen_service.py", "naia_screen_agent.js", "README.md", "NOTICE.txt", "LICENSE",
    "vendor/html2canvas-1.4.1.min.js", "vendor/html2canvas-LICENSE.txt",
})
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_CONTENT = 8 * 1024 * 1024
MAX_SETTINGS = 65536
_operation = threading.Lock()


class UpdateError(Exception):
    pass


def _safe_path(path):
    """Reject every existing symlink component, including a linked parent."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise UpdateError("심볼릭 링크가 있는 경로는 업데이트하지 않습니다.")
    return path


def _read_json(path, limit):
    path = _safe_path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise UpdateError("필요한 설정 파일을 안전하게 읽을 수 없습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        raise UpdateError("설정 파일 형식이 올바르지 않습니다.") from None
    if not isinstance(value, dict):
        raise UpdateError("설정 파일 형식이 올바르지 않습니다.")
    return value


def _write_bytes(path, content):
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def _atomic_json(path, value):
    temporary = _safe_path(path.with_name(path.name + ".android-tools-update.tmp"))
    _write_bytes(temporary, json.dumps(value, ensure_ascii=False, indent=2,
                                       allow_nan=False).encode("utf-8"))
    os.replace(temporary, path)


def _identity(meta, version):
    return (meta.get("id") == TARGET_ID and meta.get("version") == version
            and meta.get("entry") == "main.py" and meta.get("naia_ext_api") == 1)


def _payload(directory):
    meta = _read_json(directory / "payload-manifest.json", MAX_SETTINGS)
    archive = _safe_path(directory / ARCHIVE_NAME)
    if (meta.get("id") != TARGET_ID or meta.get("from_version") != FROM_VERSION
            or meta.get("to_version") != TO_VERSION or meta.get("archive") != ARCHIVE_NAME
            or not isinstance(meta.get("files"), dict) or set(meta["files"]) != FILES):
        raise UpdateError("업데이트 대상 또는 파일 목록이 일치하지 않습니다.")
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
        raise UpdateError("업데이트 묶음의 크기가 올바르지 않습니다.")
    if (meta.get("sha256") != PAYLOAD_SHA256
            or hashlib.sha256(archive.read_bytes()).hexdigest() != PAYLOAD_SHA256):
        raise UpdateError("업데이트 묶음의 해시가 일치하지 않습니다.")
    result = {}
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries) != len(FILES) or {i.filename for i in entries} != FILES:
            raise UpdateError("업데이트 묶음에 예상하지 않은 파일이 있습니다.")
        if sum(i.file_size for i in entries) > MAX_CONTENT:
            raise UpdateError("업데이트 묶음이 너무 큽니다.")
        for item in entries:
            path = PurePosixPath(item.filename)
            file_type = stat.S_IFMT(item.external_attr >> 16)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or file_type not in (0, stat.S_IFREG) or item.is_dir()):
                raise UpdateError("안전하지 않은 업데이트 파일입니다.")
            content = zipped.read(item)
            if hashlib.sha256(content).hexdigest() != meta["files"][item.filename]:
                raise UpdateError("업데이트 파일의 해시가 일치하지 않습니다.")
            result[item.filename] = content
    try:
        identity = json.loads(result["extension.json"])
    except (ValueError, UnicodeError):
        identity = {}
    if not _identity(identity, TO_VERSION):
        raise UpdateError("대체할 확장의 ID 또는 버전이 올바르지 않습니다.")
    return result


def _settings(target):
    path = _safe_path(target / "settings.json")
    settings = _read_json(path, MAX_SETTINGS) if path.exists() else {}
    # A version change must not silently expand the user's previous consent.
    return {**settings, "allow_screen": False, "allow_control": False}


def _check_tree(directory):
    total = 0
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in [*dirs, *files]:
            item = _safe_path(Path(parent) / name)
            if not item.is_dir() and not item.is_file():
                raise UpdateError("이전 확장에 지원하지 않는 파일이 있습니다.")
            if item.is_file():
                total += item.stat().st_size
                if total > MAX_CONTENT:
                    raise UpdateError("이전 확장이 예상한 크기를 초과해 업데이트를 중단했습니다.")


def _require_active(ctx):
    record = ctx._record
    if not (record.status == "loaded" and record.approved and record.enabled
            and not record.blocked and record.armed):
        raise UpdateError("업데이트 도구를 신뢰하고 활성화한 뒤 다시 눌러 주세요.")


def _same_payload(target, payload):
    return all(_safe_path(target / name).is_file()
               and (target / name).stat().st_size == len(content)
               and (target / name).read_bytes() == content
               for name, content in payload.items())


def perform_update(ctx):
    """Never run during import. The user must press the named update action."""
    _require_active(ctx)
    if not _operation.acquire(blocking=False):
        raise UpdateError("이미 업데이트 중입니다.")
    try:
        return _perform_update(ctx)
    finally:
        _operation.release()


def _perform_update(ctx):
    app = ctx._app_context
    manager = getattr(app, "extension_manager", None)
    paths = getattr(app, "runtime_paths", None)
    if manager is None or paths is None or not callable(getattr(manager, "extensions_root", None)):
        raise UpdateError("지원하는 NAIA 확장 관리자를 찾을 수 없습니다.")
    root = _safe_path(manager.extensions_root())
    directory = _safe_path(ctx.ext_dir)
    if directory != root / UPDATER_ID or not root.is_dir():
        raise UpdateError("업데이트 도구의 설치 위치가 올바르지 않습니다.")
    target = _safe_path(root / TARGET_ID)
    config = _safe_path(paths.config_dir / "extensions.json")
    backup_root = _safe_path(paths.config_dir / "android-tools" / "update-0.2.0")
    if backup_root.is_relative_to(root) or root.is_relative_to(backup_root):
        raise UpdateError("안전한 백업 폴더를 만들 수 없습니다.")
    transaction = _safe_path(root / ".android-tools-update-0.2.0")
    previous = _safe_path(transaction / "previous")
    stage = _safe_path(transaction / "new")
    payload = _payload(directory)

    # Recover the only possible interrupted rename gap before normal validation.
    if not target.exists() and previous.is_dir():
        if not _identity(_read_json(previous / "extension.json", MAX_SETTINGS), FROM_VERSION):
            raise UpdateError("중단된 업데이트 백업의 ID가 일치하지 않습니다.")
        _check_tree(previous)
        os.replace(previous, target)
    if not target.is_dir():
        raise UpdateError("기존 Android 도구 0.1.0이 설치되어 있지 않습니다.")
    current_meta = _read_json(target / "extension.json", MAX_SETTINGS)
    if _identity(current_meta, TO_VERSION):
        if not _same_payload(target, payload):
            raise UpdateError("설치된 0.2.0의 파일이 달라 덮어쓰지 않았습니다.")
        return {"updated": False, "already_updated": True, "restart_required": True}
    if not _identity(current_meta, FROM_VERSION):
        raise UpdateError("이 도구는 Android 도구 0.1.0에서 0.2.0으로만 업데이트합니다.")
    if bool(getattr(app, "is_generating", False) or getattr(app, "headless_generation_runner_active", False)):
        raise UpdateError("이미지 생성이 끝난 뒤 업데이트해 주세요.")
    _check_tree(target)
    initial_config = _read_json(config, MAX_SETTINGS)
    if not isinstance(initial_config.get("approved"), list):
        raise UpdateError("기존 확장 승인 정보를 확인할 수 없습니다.")
    old_record = next((r for r in manager.records if r.ext_id == TARGET_ID), None)
    if old_record is None or _safe_path(old_record.directory) != target:
        raise UpdateError("기존 확장의 실행 정보가 설치 위치와 다릅니다.")
    old_module = sys.modules.get("naia_ext_" + TARGET_ID)
    old_controller = getattr(old_module, "_controller", None)
    if old_record.status == "loaded" and not callable(getattr(old_controller, "close", None)):
        raise UpdateError("기존 도구를 안전하게 중지할 수 없습니다. 앱 재시작 후 다시 시도하세요.")
    if old_controller is not None and _safe_path(old_controller.ctx.ext_dir) != target:
        raise UpdateError("다른 위치의 도구가 실행 중이어서 중단했습니다.")
    if transaction.exists():
        _check_tree(transaction)
        if previous.exists():
            raise UpdateError("이전 업데이트 복구 파일이 남아 있습니다. 수동 확인이 필요합니다.")
        shutil.rmtree(transaction)
    stage.mkdir(parents=True)
    for name, content in payload.items():
        _write_bytes(stage / name, content)
    _atomic_json(stage / "settings.json", _settings(target))

    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_root.chmod(0o700)
    backup = _safe_path(backup_root / "previous-extension")
    if backup.exists():
        _check_tree(backup)
        # Keep the first complete backup. Interrupted backup copies are rejected.
        complete = _read_json(backup_root / "backup-complete.json", MAX_SETTINGS)
        if complete != {"id": TARGET_ID, "version": FROM_VERSION}:
            raise UpdateError("완료되지 않은 백업이 있어 덮어쓰지 않았습니다.")
    else:
        shutil.copytree(target, backup, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _atomic_json(backup_root / "extensions-before.json", initial_config)
        _atomic_json(backup_root / "backup-complete.json", {"id": TARGET_ID, "version": FROM_VERSION})

    revoked = False
    swapped_old = False
    installed_new = False
    try:
        with manager._lock:
            _require_active(ctx)
            latest = _read_json(config, MAX_SETTINGS)
            if not isinstance(latest.get("approved"), list):
                raise UpdateError("확장 승인 정보가 바뀌어 업데이트를 중단했습니다.")
            latest["approved"] = [v for v in latest["approved"] if v != TARGET_ID]
            latest["inactive"] = [v for v in latest.get("inactive", []) if v != TARGET_ID]
            latest["disarmed"] = [v for v in latest.get("disarmed", []) if v != TARGET_ID]
            old_flags = (old_record.approved, old_record.enabled, old_record.armed)
            old_record.approved = False
            old_record.enabled = False
            old_record.armed = False
            # Keep its loaded status until restart: never hot-load replacement code.
            try:
                _atomic_json(config, latest)
            except BaseException:
                old_record.approved, old_record.enabled, old_record.armed = old_flags
                raise
            revoked = True
        if old_controller is not None:
            old_controller.close()
        # Re-read after stopping the writer, so the last saved settings survive.
        _atomic_json(stage / "settings.json", _settings(target))
        os.replace(target, previous)
        swapped_old = True
        os.replace(stage, target)
        installed_new = True
        _atomic_json(backup_root / "completed.json", {"id": TARGET_ID, "version": TO_VERSION,
                                                    "requires_restart_and_approval": True})
    except BaseException as error:
        if swapped_old:
            try:
                if installed_new and target.exists():
                    os.replace(target, transaction / "failed-new")
                os.replace(previous, target)
            except OSError:
                raise UpdateError("업데이트 복원이 완료되지 않았습니다. 앱을 재시작하지 말고 진단을 요청해 주세요.") from None
        if revoked:
            raise UpdateError("업데이트에 실패해 이전 파일을 유지했습니다. 앱 재시작 후 기존 도구를 다시 승인해 주세요.") from error
        raise
    # The separate backup remains. The helper itself is deliberately retained.
    shutil.rmtree(transaction, ignore_errors=True)
    return {"updated": True, "already_updated": False, "restart_required": True,
            "reapproval_required": True, "version": TO_VERSION}


def register(ctx):
    def action(key):
        if key != "apply_update":
            return
        try:
            result = perform_update(ctx)
            message = ("이미 Android 도구 0.2.0이 설치되어 있습니다." if result["already_updated"]
                       else "Android 도구 0.2.0 파일을 적용했습니다. 연결 정보와 기존 설정은 유지했습니다.")
            message += " NAIA를 강제 종료하고 다시 연 뒤 Settings → Extension에서 Android 도구를 다시 신뢰하고 활성화하세요."
            ctx.log(message)
            ctx.show_toast(message, level="success")
        except Exception as error:
            message = str(error) if isinstance(error, UpdateError) else "업데이트를 완료하지 못했습니다. 기존 파일과 백업을 확인해야 합니다."
            ctx.log(message)
            ctx.show_toast(message, level="error")
    ctx.register_panel([{"key": "apply_update", "type": "action", "scope": "global",
                         "label": "Android 도구 0.2.0 적용",
                         "help": "기존 0.1.0을 중지하고 파일만 교체합니다. 앱 재시작과 도구 재승인이 필요합니다. 화면 권한은 자동으로 켜지지 않습니다."}],
                       title="Android 도구 0.2.0 업데이트", on_action=action)
