"""Load the bundled Android compatibility module through NAIA's extension API.

NAIA imports this file under a synthetic module name and does not add its
directory to sys.path. Resolve the sibling explicitly without changing the
application's import search path. Installation runs on approval and on each
enabled backend startup, before NAIA starts Random/tag warmup.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_compatibility_module():
    module_name = f"{__name__}_android_strings"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    path = Path(__file__).resolve().with_name("naia_android_strings.py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load bundled compatibility module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A failed import must not leave a partial module for NAIA's Retry action.
        sys.modules.pop(module_name, None)
        raise
    return module


def register(ctx) -> None:
    """Apply the patch without dependencies, downloads, or generation hooks."""
    compatibility = _load_compatibility_module()
    install = getattr(compatibility, "install", None)
    if not callable(install):
        raise ImportError("Bundled compatibility module must export install()")
    result = install()
    # Let failures propagate to NAIA's per-extension error and Retry UI.
    print(
        "[ext:naia_android_string_compat_v1] "
        + json.dumps(result, ensure_ascii=False, default=str),
        flush=True,
    )
