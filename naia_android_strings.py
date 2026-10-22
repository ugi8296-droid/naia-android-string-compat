"""Android string compatibility for NAIA 2.0 (v2.0.48).

Call ``install()`` once during extension startup, before background pool work.
Android's PyArrow wheel can read Parquet while omitting compute string kernels.
Use pandas' public Python string backend and scoped NAIA adapters for explicit
Arrow tag operations, nullable rating normalization, and depth character filters.
Nothing is added to, removed from, or replaced on pandas or pyarrow classes/modules.

The option also affects ``existing_arrow_series.astype(str)`` in pandas 3,
which is essential: changing the Parquet loader alone cannot fix restored pools.
Arrow remains available for Parquet I/O and existing numeric/string storage.
"""

from __future__ import annotations

import re
import sys
from functools import wraps

VERSION = "1.0.0"
_BATCH_ROWS = 20_000
_RE2_PY_WS = (
    r"[\t\n\x{0b}\f\r\x{1c}-\x{1f} \x{85}\x{a0}\x{1680}\x{2000}-\x{200a}"
    r"\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]"
)
_ARROW_LOWER_TRANSLATION = {
    ord("Σ"): ord("σ"), ord("İ"): ord("i"),
    # Arrow 24 uses Unicode 17; Python 3.14 uses Unicode 16 (host tests: 15).
    0x1C89: 0x1C8A, 0xA7CB: 0x0264, 0xA7CC: 0xA7CD, 0xA7CE: 0xA7CF,
    0xA7D2: 0xA7D3, 0xA7D4: 0xA7D5, 0xA7DA: 0xA7DB, 0xA7DC: 0x019B,
    **{code: code + 0x20 for code in range(0x10D50, 0x10D66)},
    **{code: code + 0x1B for code in range(0x16EA0, 0x16EB9)},
}


def _arrow_simple_lower(text: str) -> str:
    # Arrow uses Unicode simple lowercase, not Python's contextual/full mapping.
    # Unicode default SpecialCasing adds just these lowercase differences:
    # final capital sigma and capital dotted I. Preserve Arrow's index contract.
    if text.isascii():
        return text.lower()
    return text.translate(_ARROW_LOWER_TRANSLATION).lower()


def _normalize_rating(value) -> str:
    import pandas as pd

    # Nullable Arrow/StringDtype can yield pd.NA, whose truth value is undefined.
    if pd.isna(value):
        return ""
    return str(value or "").strip().lower()


def _build_tags_text(frame, tag_columns: list[str], heartbeat=None):
    """Build the same comma-separated index, with bounded Python temporaries."""
    import pyarrow as pa

    total = len(frame)
    chunks = []
    pool = pa.system_memory_pool()
    columns = [frame[name] for name in tag_columns]
    for start in range(0, total, _BATCH_ROWS):
        end = min(start + _BATCH_ROWS, total)
        # to_numpy handles old Arrow-backed frames without invoking any string
        # compute kernel. Replace missing fields only in this derived text index.
        # Missing must stay a missing sentinel during conversion. Passing ""
        # as na_value makes pandas call Arrow fill_null on preexisting arrays.
        values = [col.iloc[start:end].to_numpy(dtype=object, na_value=None) for col in columns]
        joined = (
            [_arrow_simple_lower(",".join("" if value is None else str(value) for value in row))
             for row in zip(*values)]
            if values else [""] * (end - start)
        )
        chunks.append(pa.array(joined, type=pa.large_string(), memory_pool=pool))
        if heartbeat is not None:
            try:
                heartbeat(end, total)
            except Exception:
                pass  # Match the upstream progress-callback contract.
    return pa.chunked_array(chunks, type=pa.large_string())


def _match_tags_text(tags_text, pattern: str):
    """Match the NAIA-generated literal/boundary patterns, missing text = false."""
    import numpy as np

    # search_runtime explicitly expands Python Unicode whitespace for RE2.
    # Python re does not understand RE2's \x{...}; restore that exact class.
    regex = re.compile(pattern.replace(_RE2_PY_WS, r"\s"))
    result = np.empty(len(tags_text), dtype=bool)
    offset = 0
    for chunk in tags_text.chunks:
        for start in range(0, len(chunk), _BATCH_ROWS):
            values = chunk.slice(start, _BATCH_ROWS).to_pylist()
            end = offset + len(values)
            result[offset:end] = [value is not None and regex.search(value) is not None for value in values]
            offset = end
    return result


class _SearchCompute:
    """Only core.search_engine sees this adapter; pyarrow.compute is untouched."""

    def __init__(self, original):
        self._original = original

    def __getattr__(self, name):
        return getattr(self._original, name)

    @staticmethod
    def _map(array, operation, *, memory_pool=None):
        import pyarrow as pa

        pool = memory_pool or pa.system_memory_pool()
        chunks = array.chunks if isinstance(array, pa.ChunkedArray) else [array]
        output = []
        for chunk in chunks:
            for start in range(0, len(chunk), _BATCH_ROWS):
                output.append(pa.array(
                    [operation(value) for value in chunk.slice(start, _BATCH_ROWS).to_pylist()],
                    type=pa.bool_(), memory_pool=pool,
                ))
        # A chunked bool array supports the same to_numpy(zero_copy_only=False)
        # used by SearchEngine without making one large Python list.
        return pa.chunked_array(output, type=pa.bool_())

    def match_substring(self, array, pattern, *, memory_pool=None):
        return self._map(array, lambda value: None if value is None else pattern in value,
                         memory_pool=memory_pool)

    def match_substring_regex(self, array, pattern, *, memory_pool=None):
        # Upstream SearchEngine uses RE2's ASCII \s at comma boundaries.
        # Keep that contract separate from runtime's explicit Unicode class.
        # RE2's \s additionally excludes vertical tab, unlike re.ASCII.
        # Translate only the generated boundary, never escaped user literals.
        prefix, suffix = r"(^|,)\s*", r"\s*(,|$)"
        if pattern.startswith(prefix) and pattern.endswith(suffix):
            literal = pattern[len(prefix):-len(suffix)]
            pattern = r"(^|,)[\t\n\f\r ]*" + literal + r"[\t\n\f\r ]*(,|$)"
        regex = re.compile(pattern, flags=re.ASCII)
        return self._map(array, lambda value: None if value is None else regex.search(value) is not None,
                         memory_pool=memory_pool)

    def fill_null(self, array, fill_value, *, memory_pool=None):
        return self._map(array, lambda value: fill_value if value is None else value,
                         memory_pool=memory_pool)


def _wrap_depth_filters(original):
    @wraps(original)
    def apply_depth_filters(frame, command):
        # Upstream fills missing character strings before astype(str); an old
        # Arrow column would otherwise still need the omitted coalesce kernel.
        if frame is not None and "character" in frame.columns:
            dtype = frame["character"].dtype
            if getattr(dtype, "storage", None) == "pyarrow" or hasattr(dtype, "pyarrow_dtype"):
                frame = frame.copy(deep=False)
                frame["character"] = frame["character"].astype(object)
        return original(frame, command)
    apply_depth_filters._naia_android_strings = True
    return apply_depth_filters


def install(*, force: bool = False) -> dict:
    """Install the Android-only workaround; ``force`` enables host regression tests.

    The pandas setting is deliberately process-wide: every NAIA ``astype(str)``
    must use Python, including ratings, depth filters, event presets and random
    pools created before this extension loads. This is a supported pandas option,
    not a monkeypatch, and does not coerce or mutate existing stored columns.
    """
    if not force and sys.platform != "android" and not hasattr(sys, "getandroidapilevel"):
        return {"enabled": False, "version": VERSION, "reason": "not_android"}

    import pandas as pd

    pd.options.mode.string_storage = "python"
    from core import search_engine, search_result_model
    from app.backend.server import search_runtime, depth_search_commands

    if not isinstance(search_engine.pc, _SearchCompute):
        search_engine.pc = _SearchCompute(search_engine.pc)
    search_runtime._build_tags_text = _build_tags_text
    search_runtime._match_tags_text = _match_tags_text
    search_result_model._normalize_rating = _normalize_rating
    if not getattr(depth_search_commands.apply_depth_filters, "_naia_android_strings", False):
        depth_search_commands.apply_depth_filters = _wrap_depth_filters(depth_search_commands.apply_depth_filters)
    return {"enabled": True, "version": VERSION, "string_storage": pd.options.mode.string_storage}
