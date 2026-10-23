import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("history_library_test", ROOT / "naia_history_library.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HistoryLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.saved = self.root / "save"
        self.saved.mkdir()
        self.context = NS(_output_root=lambda: self.output,
                          _current_save_directory=lambda: self.saved)
        self.library = module.HistoryLibrary(self.context, self.root / "state")
        self.addCleanup(self.library.close)

    def image(self, name="one.png", color="red", size=(32, 24), metadata=None):
        target = self.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", size, color) as image:
            kwargs = {}
            if metadata is not None:
                info = PngImagePlugin.PngInfo()
                for key, value in metadata.items():
                    info.add_text(key, value if isinstance(value, str) else json.dumps(value))
                kwargs["pnginfo"] = info
            image.save(target, **kwargs)
        return target

    def finish(self):
        self.library._worker.join(timeout=10)
        self.assertFalse(self.library._worker.is_alive())
        return self.library.request("job")["job"]

    def start(self, **data):
        return self.library.request("import", {"root_id": "output", "relative": "", **data})

    def test_original_bytes_metadata_and_restart_persist_without_autosave(self):
        original = self.image(metadata={"Comment": {"prompt": "a mountain", "uc": "blur", "seed": 42},
                                        "Description": "a mountain"})
        before = original.read_bytes()
        self.start(files=["one.png"])
        job = self.finish()
        self.assertEqual((job["state"], job["imported"], job["failed"]), ("completed", 1, 0))
        item = self.library.request("list")["items"][0]
        self.assertEqual((item["width"], item["height"]), (32, 24))
        self.assertEqual(self.library.asset(item["id"], "image"), (before, "image/png"))
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(list(self.saved.iterdir()), [])
        metadata = self.library.request("metadata", {"id": item["id"]})["metadata"]
        self.assertEqual(metadata["prompt"], "a mountain")
        self.assertEqual(metadata["negative"], "blur")
        self.assertEqual(metadata["parameters"]["seed"], 42)
        self.library.close()
        reopened = module.HistoryLibrary(self.context, self.root / "state")
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.request("list")["items"], [item])
        self.assertEqual(reopened.asset(item["id"], "image")[0], before)
        self.assertEqual(stat.S_IMODE(reopened.database.stat().st_mode), 0o600)

    def test_dedupe_by_content_even_renamed_and_across_jobs(self):
        original = self.image()
        (self.output / "renamed.png").write_bytes(original.read_bytes())
        self.start(files=["one.png", "renamed.png"])
        result = self.finish()
        self.assertEqual((result["processed"], result["imported"], result["duplicates"]), (2, 1, 1))
        self.start(files=["one.png"])
        self.assertEqual(self.finish()["duplicates"], 1)
        self.assertEqual(self.library.request("list")["total"], 1)
        self.assertEqual(len(list(self.library.objects.iterdir())), 1)

    def test_folder_recursive_is_optional_nonimages_and_corrupt_images(self):
        self.image()
        self.image("sub/two.webp", color="blue")
        self.image("sub/three.jpg", color="green")
        (self.output / "text.txt").write_text("not an image")
        (self.output / "fake.png").write_text("not an image")
        self.start(whole_folder=True)
        job = self.finish()
        self.assertEqual((job["imported"], job["failed"]), (1, 1))
        self.start(whole_folder=True, recursive=True)
        job = self.finish()
        self.assertEqual((job["imported"], job["duplicates"], job["failed"]), (2, 1, 1))
        self.assertEqual(self.library.request("list")["total"], 3)
        self.assertEqual(list(self.library.temporary.iterdir()), [])

    def test_browse_page_and_readonly_source_roots(self):
        for i in range(8):
            self.image(f"{i:02}.png", color=(i, 0, 0))
        (self.output / "folder").mkdir()
        (self.output / "ignore.txt").write_text("hidden by type filter")
        for target in self.output.iterdir():
            target.chmod(0o555 if target.is_dir() else 0o444)
        self.output.chmod(0o555)
        try:
            roots = self.library.request("state")["roots"]
            self.assertTrue(next(root for root in roots if root["id"] == "output")["available"])
            pages = [self.library.request("browse", {"root_id": "output", "page": p, "per_page": 3}) for p in (1, 2, 3)]
            all_entries = [entry["relative"] for page in pages for entry in page["files"] + page["dirs"]]
            self.assertEqual(len(set(all_entries)), 9)
            self.assertTrue(all(page["pages"] == 3 and page["total"] == 9 for page in pages))
            last = self.library.request("browse", {"root_id": "output", "page": 999, "per_page": 3})
            self.assertEqual(last["page"], 3)
            self.start(files=["00.png"])
            self.assertEqual(self.finish()["imported"], 1)
            self.assertEqual(stat.S_IMODE((self.output / "00.png").stat().st_mode), 0o444)
        finally:
            self.output.chmod(0o755)
            for target in self.output.iterdir():
                target.chmod(0o755 if target.is_dir() else 0o644)

    def test_traversal_absolute_uri_invalid_id_and_other_apps_rejected(self):
        self.image()
        for relative in ("../", "..", "/tmp", "foo/../bar", "foo//bar", "foo\\bar", "content://test", "Android/data", "Android/obb/other", "./one.png", "a\0b"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.library.request("browse", {"root_id": "output", "relative": relative})
        with self.assertRaises(ValueError):
            self.library.request("browse", {"root_id": str(self.output)})
        with self.assertRaises(ValueError):
            self.start(files=["../one.png"])
        with self.assertRaises(ValueError):
            self.library.asset("../index.sqlite3", "image")
        with self.assertRaises(ValueError):
            self.library.request("remove", {"id": "x"})
        self.context._current_save_directory = lambda: Path("/")
        self.assertFalse(any(root["id"] == "current_save" for root in self.library.request("state")["roots"]))

    def test_symlinks_blocked_for_listing_explicit_file_and_parent(self):
        outside = self.root / "outside"
        outside.mkdir()
        source = self.image()
        (outside / "outside.png").write_bytes(source.read_bytes())
        (self.output / "link.png").symlink_to(outside / "outside.png")
        (self.output / "linked").symlink_to(outside, target_is_directory=True)
        (self.output / "Android/data").mkdir(parents=True)
        self.image("Android/data/private.png")
        listing = self.library.request("browse", {"root_id": "output"})
        self.assertNotIn("link.png", [x["name"] for x in listing["files"]])
        self.assertNotIn("linked", [x["name"] for x in listing["dirs"]])
        self.start(files=["link.png", "linked/outside.png"])
        self.assertEqual(self.finish()["failed"], 2)
        self.start(whole_folder=True, recursive=True)
        self.assertEqual(self.finish()["imported"], 1)
        self.assertTrue((outside / "outside.png").exists())
        self.assertEqual(list(self.library.temporary.iterdir()), [])

    def test_size_pixel_limits_and_unsupported_format_leave_no_parts(self):
        self.image()
        with patch.object(module, "MAX_FILE_BYTES", 8):
            self.start(files=["one.png"])
            self.assertEqual(self.finish()["failed"], 1)
        with patch.object(module, "MAX_PIXELS", 10):
            self.start(files=["one.png"])
            self.assertEqual(self.finish()["failed"], 1)
        with Image.new("RGB", (4, 4)) as image:
            image.save(self.output / "misnamed.png", format="BMP")
        self.start(files=["misnamed.png"])
        self.assertEqual(self.finish()["failed"], 1)
        self.assertEqual(self.library.request("list")["total"], 0)
        self.assertEqual(list(self.library.temporary.iterdir()), [])

    def test_cancel_finishes_current_and_close_joins(self):
        self.image("first.png")
        self.image("second.png", color="blue")
        entered, release = threading.Event(), threading.Event()
        original = self.library._import_one
        def paused(root, parts):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test failed to release")
            return original(root, parts)
        with patch.object(self.library, "_import_one", side_effect=paused):
            self.start(files=["first.png", "second.png"])
            self.assertTrue(entered.wait(3))
            with self.assertRaises(ValueError):
                self.start(files=["second.png"])
            self.library.request("cancel")
            release.set()
            job = self.finish()
        self.assertEqual((job["state"], job["processed"], job["imported"]), ("cancelled", 1, 1))
        self.assertEqual(list(self.library.temporary.iterdir()), [])
        self.library.close()
        self.assertFalse(self.library._worker.is_alive())
        with self.assertRaises(ValueError):
            self.library.request("state")

    def test_preview_dimensions_orientation_and_cache_are_bounded(self):
        target = self.output / "rotated.jpg"
        with Image.new("RGB", (2400, 1200), "blue") as image:
            exif = Image.Exif()
            exif[274] = 6
            image.save(target, exif=exif)
        self.start(files=["rotated.jpg"])
        self.finish()
        item = self.library.request("list")["items"][0]
        for kind, maximum in (("thumb", 256), ("preview", 2048)):
            payload, mime = self.library.asset(item["id"], kind)
            self.assertEqual(mime, "image/webp")
            with Image.open(io.BytesIO(payload)) as image:
                self.assertLessEqual(max(image.size), maximum)
                self.assertGreater(image.height, image.width)
            with patch("PIL.Image.open", side_effect=AssertionError("cache must avoid decode")):
                self.assertEqual(self.library.asset(item["id"], kind)[0], payload)
        self.assertEqual(self.library.asset(item["id"], "image")[0], target.read_bytes())

    def test_large_directory_globally_sorted_with_bounded_name_selection(self):
        for i in reversed(range(1100)):
            (self.output / f"{i:04}.png").touch()
        (self.output / "Z-folder").mkdir()
        (self.output / "A-folder").mkdir()
        expected = ["A-folder", "Z-folder"] + [f"{i:04}.png" for i in range(1100)]
        original = module.heapq.nsmallest
        with patch.object(module.heapq, "nsmallest", wraps=original) as select:
            for page in (1, 17, 18, 36, 37):
                result = self.library.request("browse", {"root_id": "output", "page": page, "per_page": 30})
                actual = [item["name"] for item in result["dirs"] + result["files"]]
                self.assertEqual(actual, expected[(page - 1) * 30:page * 30])
                self.assertEqual(result["total"], 1102)
            self.assertLessEqual(max(call.args[0] for call in select.call_args_list), 512)

    def test_recursive_scan_excludes_own_library_and_cached_previews(self):
        self.image()
        nested = module.HistoryLibrary(self.context, self.output / "private")
        self.addCleanup(nested.close)
        args = {"root_id": "output", "relative": "", "whole_folder": True, "recursive": True}
        nested.request("import", args)
        nested._worker.join(5)
        self.assertEqual(nested.request("job")["job"]["imported"], 1)
        item = nested.request("list")["items"][0]
        nested.asset(item["id"], "thumb")
        nested.request("import", args)
        nested._worker.join(5)
        self.assertEqual(nested.request("job")["job"]["duplicates"], 1)
        self.assertEqual(nested.request("job")["job"]["imported"], 0)
        self.assertEqual(nested.request("browse", {"root_id": "output", "relative": "private"})["total"], 0)
        with self.assertRaises(ValueError):
            nested.request("browse", {"root_id": "output", "relative": "private/history"})
        with self.assertRaises(ValueError):
            nested.request("import", {"root_id": "output", "files": ["private/history/objects/" + item["id"] + ".png"]})

    def test_truncated_jpeg_rejected_before_becoming_gallery_item(self):
        path = self.output / "broken.jpg"
        with Image.effect_noise((256, 256), 60) as image:
            image.save(path, format="JPEG")
        raw = path.read_bytes()
        path.write_bytes(raw[:len(raw) // 2])
        self.start(files=["broken.jpg"])
        self.assertEqual(self.finish()["failed"], 1)
        self.assertEqual(self.library.request("list")["total"], 0)
        self.assertEqual(list(self.library.temporary.iterdir()), [])

    def test_parallel_preview_requests_share_one_decode_slot(self):
        for i in range(4):
            self.image(f"{i}.png", color=(i * 30, 0, 0))
        self.start(whole_folder=True)
        self.finish()
        items = self.library.request("list")["items"]
        original, lock = Image.open, threading.Lock()
        active = [0, 0]
        errors = []
        class GuardedImage:
            def __init__(self, image):
                self.image = image
            def __enter__(self):
                with lock:
                    active[0] += 1
                    active[1] = max(active)
                time.sleep(0.01)
                return self.image.__enter__()
            def __exit__(self, *args):
                try:
                    return self.image.__exit__(*args)
                finally:
                    with lock:
                        active[0] -= 1
        def run(item):
            try:
                self.library.asset(item["id"], "preview")
            except Exception as error:
                errors.append(error)
        with patch("PIL.Image.open", side_effect=lambda *a, **k: GuardedImage(original(*a, **k))):
            threads = [threading.Thread(target=run, args=(item,)) for item in items]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(active, [0, 1])

    def test_list_pagination_and_bounds(self):
        for i in range(5):
            self.image(f"{i}.png", color=(i * 30, 0, 0))
        self.start(whole_folder=True)
        self.finish()
        first = self.library.request("list", {"page": 1, "per_page": 2})
        last = self.library.request("list", {"page": 99, "per_page": 2})
        self.assertEqual((first["total"], first["pages"], len(first["items"])), (5, 3, 2))
        self.assertEqual((last["page"], len(last["items"])), (3, 1))
        self.assertIsNone(self.library.request("browse", {"root_id": "output"})["parent"])


if __name__ == "__main__":
    unittest.main()
