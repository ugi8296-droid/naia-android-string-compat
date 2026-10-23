"""Run after the tested Android Tools 0.2.0 source is final, before publishing."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("updater_payload_contract", ROOT / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def build(source):
    source = Path(source)
    data = {name: (source / name).read_bytes() for name in sorted(module.FILES)}
    if not module._identity(json.loads(data["extension.json"]), module.TO_VERSION):
        raise ValueError("Finalize the Android Tools 0.2.0 manifest first")
    archive = ROOT / module.ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as result:
        for name, content in data.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            result.writestr(info, content)
    manifest = {"id": module.TARGET_ID, "from_version": module.FROM_VERSION,
                "to_version": module.TO_VERSION, "archive": module.ARCHIVE_NAME,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in data.items()}}
    (ROOT / "payload-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    entry = ROOT / "main.py"
    source_text, count = re.subn(r'^PAYLOAD_SHA256 = "[^"\n]*"',
                                'PAYLOAD_SHA256 = "' + manifest["sha256"] + '"',
                                entry.read_text(encoding="utf-8"), flags=re.M)
    if count != 1:
        raise ValueError("Expected exactly one pinned payload digest")
    entry.write_text(source_text, encoding="utf-8")
    module.PAYLOAD_SHA256 = manifest["sha256"]
    module._payload(ROOT)
    print(json.dumps({"archive": str(archive), "sha256": manifest["sha256"], "files": len(data)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default=str(ROOT.parent / "android-tools"))
    build(parser.parse_args().source)
