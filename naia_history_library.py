"""Persistent, disk-backed image imports for the Android history browser.

Source directories are read-only capabilities. No source file is moved, rewritten,
or registered as an auto-save result. Only one file is copied/decoded at a time.
"""
from __future__ import annotations

import hashlib
import heapq
from contextlib import contextmanager
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000
CHUNK_BYTES = 256 * 1024
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
_ID = re.compile(r"^[0-9a-f]{64}$")


def _number(value, default, maximum):
    if isinstance(value, bool):
        raise ValueError("페이지 번호가 올바르지 않습니다.")
    try:
        return min(maximum, max(1, int(default if value is None else value)))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("페이지 번호가 올바르지 않습니다.") from None


def _parts(value):
    if not isinstance(value, str) or len(value) > 4096 or any(x in value for x in ("\\", ":", "\0")):
        raise ValueError("목록에서 파일이나 폴더를 선택하세요.")
    if value == "":
        return ()
    parts = tuple(value.split("/"))
    if len(parts) > 64 or any(not p or p in (".", "..") for p in parts):
        raise ValueError("목록에서 파일이나 폴더를 선택하세요.")
    lowered = tuple(p.casefold() for p in parts)
    if any(lowered[i] == "android" and lowered[i + 1] in ("data", "obb") for i in range(len(parts) - 1)):
        raise ValueError("다른 앱의 전용 폴더에는 접근할 수 없습니다.")
    return parts


def _blocked_absolute(path):
    lowered = tuple(p.casefold() for p in path.parts)
    if any(lowered[i] == "android" and lowered[i + 1] in ("data", "obb") for i in range(len(lowered) - 1)):
        return True
    if str(path) in {"/", "/data", "/storage", "/storage/emulated"}:
        return True
    return any(path == Path(p) or Path(p) in path.parents for p in ("/proc", "/sys", "/dev", "/etc", "/root"))


def _public_error(exc):
    # Do not send absolute app paths, sqlite internals, or Python tracebacks to UI.
    if isinstance(exc, ValueError):
        return str(exc)[:240]
    if isinstance(exc, PermissionError):
        return "이 파일이나 폴더를 읽을 권한이 없습니다."
    if isinstance(exc, FileNotFoundError):
        return "파일이나 폴더가 없어졌습니다."
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "이미지를 보관할 저장공간이 부족합니다."
    return "이미지를 읽거나 보관하지 못했습니다."


class HistoryLibrary:
    def __init__(self, context, state_root):
        self.context = getattr(context, "_app_context", context)
        self.root = Path(state_root) / "history"
        self.objects = self.root / "objects"
        self.cache = self.root / "cache"
        self.temporary = self.root / "temporary"
        for path in (self.root, self.objects, self.cache, self.temporary):
            if path.is_symlink():
                raise ValueError("이미지 보관함 경로가 올바르지 않습니다.")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        self._canonical_root = self.root.resolve()
        self.database = self.root / "index.sqlite3"
        if self.database.is_symlink():
            raise ValueError("이미지 보관함 경로가 올바르지 않습니다.")
        self._lock = threading.RLock()
        self._decode_lock = threading.BoundedSemaphore(1)
        self._closed = False
        self._cancel = threading.Event()
        self._worker = None
        self._job = None
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, extension TEXT NOT NULL,
                mime TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                size INTEGER NOT NULL, created_ns INTEGER NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS images_created ON images(created_ns DESC, id)")
        self.database.chmod(0o600)
        # A killed process can leave only private .part files, never partial originals.
        for path in self.temporary.glob("*.part"):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_open(self):
        if self._closed:
            raise ValueError("이미지 보관함이 종료되었습니다. 다시 열어 주세요.")

    def _roots(self):
        candidates = []
        output = None
        try:
            getter = getattr(self.context, "_output_root", None)
            output = getter() if callable(getter) else getattr(getattr(self.context, "runtime_paths", None), "output_dir", None)
        except Exception:
            pass
        if output is not None:
            candidates.append(("output", "앱 출력 폴더", output))
        current = None
        try:
            getter = getattr(self.context, "_current_save_directory", None)
            if callable(getter):
                current = getter()
            else:
                service = getattr(self.context, "_save_service", None)
                if callable(service):
                    current = service().current_save_directory()
        except Exception:
            pass
        if current is None:
            state = getattr(self.context, "save_directory_state", {}) or {}
            raw = state.get("base_path", "output") if isinstance(state, dict) else "output"
            if isinstance(raw, str) and "://" not in raw:
                current = output if raw == "output" else Path(raw)
                if current is not None and not Path(current).is_absolute() and output is not None:
                    current = Path(output) / current
        if current is not None:
            candidates.append(("current_save", "현재 저장 폴더", current))
        # Canonicalize the OS-owned shared root, then prohibit symlinks below it.
        shared = Path("/storage/emulated/0")
        try:
            from java import jclass
            shared = Path(str(jclass("android.os.Environment").getExternalStorageDirectory().getCanonicalPath()))
        except Exception:
            if not shared.exists() and Path("/sdcard").exists():
                shared = Path("/sdcard")
        candidates.append(("shared", "내장 저장공간", shared))
        roots, seen = {}, set()
        for root_id, label, value in candidates:
            try:
                path = Path(value).expanduser().resolve()
                if not path.is_absolute() or _blocked_absolute(path) or path in seen:
                    continue
                seen.add(path)
                roots[root_id] = (label, path)
            except (TypeError, ValueError, OSError, RuntimeError):
                continue
        return roots

    def _root(self, root_id):
        roots = self._roots()
        if not isinstance(root_id, str) or root_id not in roots:
            raise ValueError("목록에서 저장 위치를 선택하세요.")
        return roots[root_id][1]

    @staticmethod
    def _open_directory(root, parts=()):
        # dir_fd + O_NOFOLLOW also protects against an entry replaced after browsing.
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(root, flags)
        try:
            for part in parts:
                following = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @classmethod
    def _open_source(cls, root, parts):
        if not parts:
            raise ValueError("이미지 파일을 선택하세요.")
        descriptor = cls._open_directory(root, parts[:-1])
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=descriptor)
        finally:
            os.close(descriptor)
        try:
            details = os.fstat(file_fd)
            if not stat.S_ISREG(details.st_mode):
                raise ValueError("일반 이미지 파일만 가져올 수 있습니다.")
            if details.st_size > MAX_FILE_BYTES:
                raise ValueError("64MB 이하 이미지 파일만 가져올 수 있습니다.")
            return os.fdopen(file_fd, "rb")
        except BaseException:
            os.close(file_fd)
            raise

    def _job_snapshot(self):
        with self._lock:
            return dict(self._job) if self._job is not None else None

    def _total(self):
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM images").fetchone()[0]

    def _state(self):
        roots = []
        for root_id, (label, path) in self._roots().items():
            try:
                descriptor = self._open_directory(path)
                os.close(descriptor)
                available = True
            except OSError:
                available = False
            roots.append({"id": root_id, "label": label, "available": available})
        return {"roots": roots, "total": self._total(), "job": self._job_snapshot()}

    def _allow_source(self, root, parts):
        path = root.joinpath(*parts)
        if path == self._canonical_root or self._canonical_root in path.parents:
            raise ValueError("보관함 내부 파일은 다시 가져올 수 없습니다.")

    def _browse(self, data):
        root_id = data.get("root_id")
        root, parts = self._root(root_id), _parts(data.get("relative", ""))
        self._allow_source(root, parts)
        page = _number(data.get("page"), 1, 10000)
        per_page = _number(data.get("per_page"), 60, 100)
        descriptor = self._open_directory(root, parts)
        total = 0

        def entries(after=None):
            nonlocal total
            # A fresh descriptor starts each scan at offset zero. Keep at most
            # 512 names while selecting globally sorted pages, even for huge dirs.
            scan_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=descriptor)
            try:
                with os.scandir(scan_fd) as scan:
                    for entry in scan:
                        try:
                            relative = "/".join((*parts, entry.name))
                            child = _parts(relative)
                            self._allow_source(root, child)
                            if entry.is_symlink():
                                continue
                            directory = entry.is_dir(follow_symlinks=False)
                            if not directory and (not entry.is_file(follow_symlinks=False) or Path(entry.name).suffix.lower() not in EXTENSIONS):
                                continue
                            key = (0 if directory else 1, entry.name.casefold(), entry.name)
                            if after is None:
                                total += 1
                            if after is not None and key <= after:
                                continue
                            item = {"name": entry.name, "relative": relative}
                            if not directory:
                                item["size"] = entry.stat(follow_symlinks=False).st_size
                            yield (*key, item)
                        except (OSError, ValueError):
                            continue
            finally:
                os.close(scan_fd)
        try:
            after = None
            skip = (page - 1) * per_page
            selected = []
            while True:
                batch = heapq.nsmallest(min(512, skip + per_page), entries(after), key=lambda entry: entry[:3])
                pages = max(1, math.ceil(total / per_page))
                if after is None and page > pages:
                    page = pages
                    skip = (page - 1) * per_page
                if skip < len(batch):
                    selected = batch[skip:skip + per_page]
                    # A page can straddle a 512-name selection boundary.
                    if len(selected) < per_page and batch:
                        more = heapq.nsmallest(per_page - len(selected), entries(batch[-1][:3]), key=lambda entry: entry[:3])
                        selected.extend(more)
                    break
                if not batch:
                    break
                skip -= len(batch)
                after = batch[-1][:3]
        finally:
            os.close(descriptor)
        return {"root_id": root_id, "relative": "/".join(parts),
                "parent": "/".join(parts[:-1]) if parts else None,
                "dirs": [item for kind, _, _, item in selected if kind == 0],
                "files": [item for kind, _, _, item in selected if kind == 1],
                "page": page, "pages": pages, "total": total}

    def _walk(self, root, parts, recursive):
        self._allow_source(root, parts)
        if self._cancel.is_set():
            return
        descriptor = self._open_directory(root, parts)
        try:
            with os.scandir(descriptor) as scan:
                for entry in scan:
                    if self._cancel.is_set():
                        break
                    try:
                        child = _parts("/".join((*parts, entry.name)))
                        self._allow_source(root, child)
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in EXTENSIONS:
                            yield child
                        elif recursive and entry.is_dir(follow_symlinks=False):
                            yield from self._walk(root, child, recursive)
                    except ValueError:
                        continue
                    except OSError as exc:
                        self._record_failure(exc)
        finally:
            os.close(descriptor)

    def _start_import(self, data):
        root = self._root(data.get("root_id"))
        parts = _parts(data.get("relative", ""))
        self._allow_source(root, parts)
        whole, recursive = data.get("whole_folder", False), data.get("recursive", False)
        if not isinstance(whole, bool) or not isinstance(recursive, bool):
            raise ValueError("폴더 선택이 올바르지 않습니다.")
        descriptor = self._open_directory(root, parts)
        os.close(descriptor)
        selected = None
        if not whole:
            files = data.get("files")
            if not isinstance(files, list) or not files or len(files) > 10000:
                raise ValueError("가져올 이미지를 선택하세요. 한 번에 최대 10000장을 선택할 수 있습니다.")
            selected = []
            for name in files:
                file_parts = _parts(name)
                self._allow_source(root, file_parts)
                if not file_parts or file_parts[:len(parts)] != parts or Path(file_parts[-1]).suffix.lower() not in EXTENSIONS:
                    raise ValueError("선택한 폴더 안의 PNG, JPEG, WebP 이미지만 가져올 수 있습니다.")
                selected.append(file_parts)
        with self._lock:
            self._ensure_open()
            if self._worker is not None and self._worker.is_alive():
                raise ValueError("이미 가져오는 중입니다. 완료하거나 중지한 뒤 다시 선택하세요.")
            self._cancel.clear()
            self._job = {"id": uuid.uuid4().hex, "state": "running", "processed": 0,
                         "imported": 0, "duplicates": 0, "failed": 0, "error": "",
                         "current": "", "total": len(selected) if selected is not None else None}
            self._worker = threading.Thread(target=self._run_import, args=(root, parts, selected, recursive), name="naia-image-import", daemon=True)
            self._worker.start()
            return {"job": dict(self._job)}

    def _record_failure(self, exc):
        with self._lock:
            self._job["failed"] += 1
            self._job["error"] = _public_error(exc)

    def _run_import(self, root, parts, selected, recursive):
        try:
            sequence = iter(selected) if selected is not None else self._walk(root, parts, recursive)
            for file_parts in sequence:
                if self._cancel.is_set():
                    break
                with self._lock:
                    self._job["current"] = file_parts[-1]
                try:
                    result = self._import_one(root, file_parts)
                    with self._lock:
                        self._job["duplicates" if result == "duplicate" else "imported"] += 1
                except Exception as exc:
                    self._record_failure(exc)
                    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
                        raise
                finally:
                    with self._lock:
                        self._job["processed"] += 1
            with self._lock:
                self._job["state"] = "cancelled" if self._cancel.is_set() else "completed"
        except Exception as exc:
            with self._lock:
                self._job["state"] = "error"
                self._job["error"] = _public_error(exc)
        finally:
            with self._lock:
                self._job["current"] = ""

    def _import_one(self, root, parts):
        from PIL import Image
        self._allow_source(root, parts)
        temporary = self.temporary / (uuid.uuid4().hex + ".part")
        digest, size = hashlib.sha256(), 0
        try:
            with self._open_source(root, parts) as source, temporary.open("xb") as target:
                temporary.chmod(0o600)
                while True:
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError("64MB 이하 이미지 파일만 가져올 수 있습니다.")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            image_id = digest.hexdigest()
            with self._connect() as connection:
                if connection.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone():
                    return "duplicate"
            with self._decode_lock:
                with Image.open(temporary) as image:
                    if image.format not in FORMATS:
                        raise ValueError("PNG, JPEG, WebP 이미지만 가져올 수 있습니다.")
                    width, height = image.size
                    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                        raise ValueError("4000만 화소 이하 이미지 파일만 가져올 수 있습니다.")
                    extension, mime = FORMATS[image.format]
                    image.verify()
                # JPEG verify() checks headers only; decode once so corrupt pixel
                # data never enters the persistent gallery as a successful import.
                with Image.open(temporary) as image:
                    image.load()
            destination = self.objects / (image_id + extension)
            os.replace(temporary, destination)
            try:
                with self._connect() as connection:
                    connection.execute("INSERT INTO images VALUES (?,?,?,?,?,?,?,?)",
                                       (image_id, parts[-1][:255], extension, mime, width, height, size, time.time_ns()))
            except Exception:
                # Disk cache is private; remove only this unindexed copy, never source.
                with self._connect() as connection:
                    exists = connection.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone()
                if not exists:
                    destination.unlink(missing_ok=True)
                raise
            return "imported"
        finally:
            temporary.unlink(missing_ok=True)

    def _list(self, data):
        page = _number(data.get("page"), 1, 1000000)
        per_page = _number(data.get("per_page"), 30, 100)
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            pages = max(1, math.ceil(total / per_page))
            page = min(page, pages)
            rows = connection.execute("SELECT id,name,width,height,size FROM images ORDER BY created_ns DESC,id LIMIT ? OFFSET ?",
                                      (per_page, (page - 1) * per_page)).fetchall()
        return {"items": [dict(row) for row in rows], "page": page, "pages": pages, "total": total}

    def _item(self, image_id):
        if not isinstance(image_id, str) or not _ID.fullmatch(image_id):
            raise ValueError("이미지를 목록에서 선택하세요.")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None:
            raise ValueError("보관함에 없는 이미지입니다.")
        path = self.objects / (row["id"] + row["extension"])
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Library image missing")
        return row, path

    @staticmethod
    def _safe_metadata(value, depth=0):
        if depth > 5:
            return None
        if isinstance(value, str):
            return value[:65536]
        if isinstance(value, (bool, int)) or value is None:
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, bytes):
            return value[:65536].decode("utf-8", "replace")
        if isinstance(value, dict):
            return {str(k)[:200]: HistoryLibrary._safe_metadata(v, depth + 1) for k, v in list(value.items())[:100]}
        if isinstance(value, (list, tuple)):
            return [HistoryLibrary._safe_metadata(v, depth + 1) for v in value[:100]]
        return str(value)[:1024]

    def _metadata(self, image_id):
        from PIL import Image
        row, path = self._item(image_id)
        with self._decode_lock:
            with Image.open(path) as image:
                info = {}
                for key, value in list(image.info.items())[:100]:
                    if key in ("icc_profile", "exif"):
                        continue
                    value = self._safe_metadata(value)
                    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                        try:
                            value = self._safe_metadata(json.loads(value))
                        except (ValueError, TypeError):
                            pass
                    info[key] = value
                extracted = {}
                try:
                    from utils.image_info import ImageMetadataExtractor
                    extracted = self._safe_metadata(ImageMetadataExtractor.extract_metadata(image) or {})
                    if not isinstance(extracted, dict):
                        extracted = {}
                except Exception:
                    pass
        comment = info.get("Comment", {})
        comment = comment if isinstance(comment, dict) else {}
        generation = info.get("naia_generation_params", {})
        generation = generation if isinstance(generation, dict) else {}
        prompt_context = info.get("naia_prompt_context", {})
        prompt_context = prompt_context if isinstance(prompt_context, dict) else {}
        metadata = {"name": row["name"], "width": row["width"], "height": row["height"],
                    "size": row["size"], "mime_type": row["mime"], "info": info, "extracted": extracted}
        metadata["prompt"] = str(prompt_context.get("main_prompt") or prompt_context.get("final_prompt") or generation.get("input") or generation.get("prompt") or extracted.get("prompt") or comment.get("prompt") or info.get("Description") or info.get("prompt") or "")[:65536]
        metadata["negative"] = str(generation.get("negative_prompt") or generation.get("uc") or extracted.get("negative") or extracted.get("uc") or comment.get("uc") or comment.get("negative_prompt") or info.get("negative") or "")[:65536]
        parameters = extracted.get("parameters", {})
        parameters = parameters if isinstance(parameters, dict) else {}
        metadata["parameters"] = {key: self._safe_metadata(generation.get(key, extracted.get(key, parameters.get(key, comment.get(key)))))
                                  for key in ("seed", "steps", "sampler", "scale", "cfg_scale", "model")
                                  if any(source.get(key) is not None for source in (generation, extracted, parameters, comment))}
        return {"metadata": metadata}

    def request(self, action, data=None):
        self._ensure_open()
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("요청 형식이 올바르지 않습니다.")
        if action == "state":
            return self._state()
        if action == "browse":
            return self._browse(data)
        if action == "import":
            return self._start_import(data)
        if action == "list":
            return self._list(data)
        if action == "job":
            return {"job": self._job_snapshot()}
        if action == "cancel":
            self._cancel.set()
            return {"job": self._job_snapshot()}
        if action == "metadata":
            return self._metadata(data.get("id"))
        raise ValueError("지원하지 않는 보관함 작업입니다.")

    def asset(self, image_id, kind="thumb"):
        from PIL import Image, ImageOps
        self._ensure_open()
        row, path = self._item(image_id)
        if kind == "image":
            return path.read_bytes(), row["mime"]
        if kind not in ("thumb", "preview"):
            raise ValueError("지원하지 않는 이미지 보기입니다.")
        cached = self.cache / (image_id + "-" + kind + ".webp")
        with self._decode_lock:
            if cached.is_file() and not cached.is_symlink():
                return cached.read_bytes(), "image/webp"
            max_side = 256 if kind == "thumb" else 2048
            with Image.open(path) as opened:
                if opened.width * opened.height > MAX_PIXELS:
                    raise ValueError("4000만 화소 이하 이미지 파일만 볼 수 있습니다.")
                opened.draft("RGB", (max_side, max_side))
                # Shrink before orientation copying to avoid two full decoded copies.
                opened.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                image = ImageOps.exif_transpose(opened)
                try:
                    buffer = io.BytesIO()
                    image.save(buffer, format="WEBP", quality=82 if kind == "thumb" else 88, method=3)
                    result = buffer.getvalue()
                finally:
                    if image is not opened:
                        image.close()
            temporary = self.temporary / (uuid.uuid4().hex + ".part")
            try:
                with temporary.open("xb") as target:
                    temporary.chmod(0o600)
                    target.write(result)
                os.replace(temporary, cached)
            finally:
                temporary.unlink(missing_ok=True)
            return result, "image/webp"

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel.set()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()
