"""Bounded filesystem folder picker for an existing NAIA headless runtime.

This is deliberately not an Android SAF adapter. Native content URIs never enter
pathlib or NAIA's filesystem-only output service. Merely browsing never writes.
"""
from __future__ import annotations

import copy
import base64
import json
import os
from pathlib import Path
import secrets
import threading


class StorageError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _failure(exc):
    if isinstance(exc, StorageError):
        code, message = exc.code, exc.message
    elif isinstance(exc, FileExistsError):
        code, message = "already_exists", "같은 이름의 항목이 이미 있습니다. 다른 이름을 입력해 주세요."
    elif isinstance(exc, PermissionError):
        code, message = "permission_denied", "이 폴더에 접근하거나 저장할 권한이 없습니다. 다른 폴더를 선택해 주세요."
    elif isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        code, message = "not_directory", "폴더가 없거나 더 이상 사용할 수 없습니다. 목록을 새로고침해 주세요."
    else:
        code, message = "storage_error", "폴더 작업을 완료하지 못했습니다. 저장 공간과 접근 권한을 확인해 주세요."
    return {"ok": False, "error": {"code": code, "message": message}}


class StorageDirectoryService:
    MAX_ENTRIES = 300
    MAX_SCAN = 2000

    def __init__(self, context, allowed_roots=None):
        self.context = context
        self._lock = threading.RLock()
        self._roots = {}
        self._default_output = Path(context._output_root()).expanduser().resolve()
        if allowed_roots is None:
            output = self._default_output
            candidates = [("앱 이미지 폴더", output)]
            raw = str(context.save_directory_state.get("base_path") or "output")
            if "://" not in raw:
                current = Path(raw).expanduser()
                if raw == "output":
                    current = output
                elif not current.is_absolute():
                    current = output / current
                candidates.append(("현재 저장 폴더", current))
            candidates.extend([("내부 공유 저장소", Path("/storage/emulated/0")),
                               ("공유 저장소", Path("/sdcard"))])
        else:
            candidates = [(str(value[0]), Path(value[1])) if isinstance(value, tuple)
                          else (Path(value).name or "폴더", Path(value))
                          for value in allowed_roots]
        seen = set()
        for label, path in candidates:
            try:
                canonical = path.expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            # Never turn a mistaken base_path of '/' into a filesystem browser.
            if str(canonical) in {"/", "/data", "/etc", "/proc", "/sys", "/dev", "/root", "/storage", "/storage/emulated"}:
                continue
            if any(canonical.is_relative_to(Path(system)) for system in ("/etc", "/proc", "/sys", "/dev", "/root")):
                continue
            if canonical in seen:
                continue
            seen.add(canonical)
            self._roots[secrets.token_urlsafe(12)] = (label, canonical)

    @staticmethod
    def _parts(relative):
        if not isinstance(relative, str) or ":" in relative or "\\" in relative or "\x00" in relative:
            raise StorageError("invalid_path", "올바른 폴더를 목록에서 선택해 주세요.")
        if relative == "":
            return []
        parts = relative.split("/")
        if any(not p or p.startswith(".") or any(ord(c) < 32 for c in p) for p in parts):
            raise StorageError("invalid_path", "올바른 폴더를 목록에서 선택해 주세요.")
        # App-private shared storage is not a useful user image destination.
        if any(a.casefold() == "android" and b.casefold() in {"data", "obb"}
               for a, b in zip(parts, parts[1:])):
            raise StorageError("restricted_path", "앱 전용 폴더는 선택할 수 없습니다. Pictures 또는 다른 이미지 폴더를 선택해 주세요.")
        return parts

    def _open_directory(self, root_id, relative):
        root = self._roots.get(root_id)
        if root is None:
            raise StorageError("unknown_root", "폴더 목록이 만료되었습니다. 새로고침해 주세요.")
        parts = self._parts(relative)
        base = root[1]
        target = base.joinpath(*parts)
        # Resolve confinement first, then use no-follow directory descriptors for
        # every operation. A swapped child symlink cannot redirect list/probe/mkdir.
        if not target.resolve().is_relative_to(base):
            raise StorageError("restricted_path", "선택 가능한 폴더 범위를 벗어났습니다.")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = self._open_absolute(base)
        try:
            for part in parts:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd, target
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _open_absolute(path):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        # Android app data ancestors may be searchable but not readable. O_PATH
        # traverses those without requiring directory-list permission. Only the
        # selected leaf needs O_RDONLY for scandir and the probe operations.
        if not hasattr(os, "O_PATH"):
            if path.resolve() != path:
                raise StorageError("directory_changed", "폴더가 변경되었습니다. 다시 선택해 주세요.")
            fd = os.open(path, flags)
            if path.resolve() != path:
                os.close(fd)
                raise StorageError("directory_changed", "폴더가 변경되었습니다. 다시 선택해 주세요.")
            return fd
        ancestor_flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(path.anchor, ancestor_flags)
        try:
            for index, part in enumerate(path.parts[1:], 1):
                child = os.open(part, flags if index == len(path.parts) - 1 else ancestor_flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise

    def state(self):
        roots = []
        for root_id, (label, path) in self._roots.items():
            available = False
            try:
                fd, _ = self._open_directory(root_id, "")
                os.close(fd)
                available = True
            except (OSError, StorageError, RuntimeError):
                pass
            roots.append({"id": root_id, "label": label, "path": str(path),
                          "available": available,
                          "creatable": path == self._default_output and not path.exists() and path.parent.is_dir(),
                          "writable": available and os.access(path, os.W_OK | os.X_OK)})
        raw = str(self.context.save_directory_state.get("base_path") or "output")
        result = {"ok": True, "roots": roots, "current_base_path": raw,
                  "current_save_directory": "", "can_choose": any(r["available"] or r["creatable"] for r in roots),
                  "capabilities": {"native_saf": False, "filesystem": True, "create_directory": True}}
        if "://" in raw:
            result.update(_failure(StorageError("unsupported_uri", "Android 시스템 폴더 주소는 이 버전에서 사용할 수 없습니다. 접근 가능한 폴더를 목록에서 선택해 주세요.")))
        else:
            try:
                result["current_save_directory"] = str(self.context._save_service().current_save_directory())
            except Exception:
                result.update(_failure(StorageError("state_error", "현재 저장 폴더를 확인하지 못했습니다.")))
        return result

    def list_directories(self, root_id, relative=""):
        try:
            fd, target = self._open_directory(root_id, relative)
            entries, scanned, truncated = [], 0, False
            try:
                with os.scandir(fd) as listing:
                    for entry in listing:
                        scanned += 1
                        if scanned > self.MAX_SCAN:
                            truncated = True
                            break
                        child = "/".join(filter(None, [relative, entry.name]))
                        try:
                            self._parts(child)
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except (StorageError, OSError):
                            continue
                        if len(entries) >= self.MAX_ENTRIES:
                            truncated = True
                            break
                        entries.append({"name": entry.name, "relative": child})
                entries.sort(key=lambda row: row["name"].casefold())
                return {"ok": True, "root_id": root_id, "relative": relative,
                        "parent_relative": relative.rpartition("/")[0] if relative else None,
                        "directories": entries, "truncated": truncated,
                        "can_select": os.access(target, os.W_OK | os.X_OK),
                        "selection_requires_write_check": True}
            finally:
                os.close(fd)
        except (OSError, StorageError, RuntimeError) as exc:
            return _failure(exc)

    @staticmethod
    def _probe(fd):
        name = "NAIA-write-check-" + secrets.token_hex(12) + ".png"
        file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            data = memoryview(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            while data:
                written = os.write(file_fd, data)
                if written <= 0:
                    raise OSError("short write")
                data = data[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
            os.unlink(name, dir_fd=fd)

    def apply_selection(self, root_id, relative=""):
        with self._lock:
            try:
                root = self._roots.get(root_id)
                if relative == "" and root and root[1] == self._default_output and not root[1].exists():
                    # Only this explicit select action may initialize NAIA's own
                    # output leaf. Never create shared roots or a typed base path.
                    parent_fd = self._open_absolute(root[1].parent)
                    try:
                        try:
                            os.mkdir(root[1].name, 0o755, dir_fd=parent_fd)
                        except FileExistsError:
                            pass
                    finally:
                        os.close(parent_fd)
                fd, target = self._open_directory(root_id, relative)
                try:
                    self._probe(fd)
                    # The original service stores a path, so reject replacement
                    # between validation and committing that path as well.
                    disk_stat, opened_stat = os.stat(target), os.fstat(fd)
                    if target.resolve() != target or (disk_stat.st_dev, disk_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
                        raise StorageError("directory_changed", "폴더가 변경되었습니다. 다시 선택해 주세요.")
                finally:
                    os.close(fd)
                had_base = "base_path" in self.context.save_directory_state
                old_base = copy.deepcopy(self.context.save_directory_state.get("base_path"))
                try:
                    state = self.context._save_service().set_save_directory_param(
                        "base_path", str(target), client_host="127.0.0.1")
                    settings = json.loads(Path(self.context._save_path("app_settings.json")).read_text(encoding="utf-8"))
                    persisted = settings.get("remote_web", {}).get("save_directory_state", {}).get("base_path")
                    if not isinstance(state, dict) or persisted != str(target) or state.get("base_path") != str(target):
                        raise RuntimeError("destination was not persisted")
                except Exception:
                    if had_base:
                        self.context.save_directory_state["base_path"] = old_base
                    else:
                        self.context.save_directory_state.pop("base_path", None)
                    try:
                        self.context.save_remote_ui_state()
                    except Exception:
                        pass
                    raise StorageError("save_failed", "저장 폴더 설정이 저장되었는지 확인하지 못했습니다. 다시 시도해 주세요.")
                return {"ok": True, "applied": True, "state": state}
            except (OSError, StorageError, RuntimeError) as exc:
                return _failure(exc)

    def create_directory(self, root_id, relative="", name=""):
        try:
            if not isinstance(name, str) or len(name.encode("utf-8")) > 255 or len(self._parts(name)) != 1:
                raise StorageError("invalid_name", "폴더 이름 한 개를 입력해 주세요. 경로나 숨김 폴더 이름은 사용할 수 없습니다.")
            child = "/".join(filter(None, [relative, name]))
            self._parts(child)
            fd, _ = self._open_directory(root_id, relative)
            try:
                os.mkdir(name, 0o755, dir_fd=fd)
            finally:
                os.close(fd)
            return {"ok": True, "created": True, "root_id": root_id, "relative": child, "name": name}
        except (OSError, StorageError, RuntimeError) as exc:
            return _failure(exc)
