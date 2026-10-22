"""Exact structured-output adapters for NAIA 2.0.48 Assist grammar factories.

The original grammar remains a str for llama.cpp. Cloud calls use its attached
schema, validate the completed JSON locally, then restore the exact shape each
unchanged NAIA parser expects. Unknown grammars fail closed.
"""
from __future__ import annotations

import copy
import functools
import importlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable


class SchemaError(ValueError):
    pass


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def string(lo=0, hi=None, pattern=None, enum=None):
    out = {"type": "string"}
    if lo:
        out["minLength"] = lo
    if hi is not None:
        out["maxLength"] = hi
    if pattern is not None:
        out["pattern"] = pattern
    if enum is not None:
        out["enum"] = list(dict.fromkeys(enum))
    return out


def array(items, lo=0, hi=None):
    out = {"type": "array", "items": items, "minItems": lo}
    if hi is not None:
        out["maxItems"] = hi
    return out


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate(value: Any, schema: dict, path: str = "$") -> None:
    """Validate the deliberately small schema vocabulary emitted by this module."""
    kind = schema.get("type")
    good = {"object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str), "integer": type(value) is int}.get(kind)
    if not good:
        raise SchemaError(f"Invalid {kind} at {path}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"Value outside allowed choices at {path}")
    if kind == "object":
        props = schema["properties"]
        if set(value) != set(props):
            raise SchemaError(f"Missing or additional fields at {path}")
        for key, child in props.items():
            validate(value[key], child, f"{path}.{key}")
    elif kind in ("string", "array"):
        suffix = "Length" if kind == "string" else "Items"
        if len(value) < schema.get("min" + suffix, 0) or len(value) > schema.get("max" + suffix, float("inf")):
            raise SchemaError(f"Invalid length at {path}")
        if kind == "string" and "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise SchemaError(f"Invalid characters at {path}")
        if kind == "array":
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")
    elif kind == "integer":
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise SchemaError(f"Invalid number at {path}")


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise SchemaError("Duplicate JSON field")
        out[key] = value
    return out


@dataclass(frozen=True)
class OutputContract:
    name: str
    schema: dict
    decode: Callable[[Any], str] = compact

    def completed_text(self, text: str) -> str:
        try:
            value = json.loads(text, object_pairs_hook=_no_duplicates,
                               parse_constant=lambda _: (_ for _ in ()).throw(SchemaError("Non-finite JSON")))
        except (ValueError, TypeError) as exc:
            raise SchemaError("The completed response was not valid structured JSON") from exc
        validate(value, self.schema)
        return self.decode(value)


class SchemaGrammar(str):
    def __new__(cls, original: str, contract: OutputContract):
        value = super().__new__(cls, original)
        value.contract = contract
        return value


SENTENCE = r"[A-Za-z0-9 ,.'!?-]*"
TAG = r"[a-z0-9 ()'.:_-]*"
REFINE_TAG = r"[a-z0-9 ()'.:-]*"


def grammar_contract(module_name: str, function: str, *args, **kwargs) -> OutputContract:
    """Factories consume original arguments, so dynamic constraints never get lost."""
    m = importlib.import_module(module_name)
    name = module_name.rsplit(".", 1)[-1] + "_" + function
    if module_name == "core.assist_v2":
        source = args[0] if args else kwargs.get("schema")
        return OutputContract(name, copy.deepcopy(source or m.SCHEMA))
    if module_name == "core.assist_translate":
        text = args[0] if args else kwargs["text"]
        return OutputContract(name, obj({"en": string(1, m.literal_limit(text), r"[A-Za-z0-9 ,.'?!()-]*")}))
    if module_name == "core.assist_direct":
        if function == "situation_grammar":
            return OutputContract(name, obj({"sentence": string(9, m.MAX_SITUATION, r"[A-Z][A-Za-z0-9 ,.'!?-]*")}),
                                  lambda value: value["sentence"])
        return OutputContract(name, obj({"tags": array(string(1, 40, TAG), 1, m.MAX_TAGS),
                                         "sentence": string(10, m.MAX_SENTENCE, SENTENCE)}))
    if module_name == "core.assist_refine":
        # Upstream grammar permits six; its parser intentionally truncates to 3/4.
        return OutputContract(name, obj({"remove": array(string(1, 40, REFINE_TAG), hi=6),
                                         "add": array(string(1, 40, REFINE_TAG), hi=6),
                                         "sentence": string(10, m.MAX_SENTENCE, SENTENCE)}))
    if module_name == "core.assist_followup":
        removable = args[0] if args else kwargs["removable"]
        names = [t for t in dict.fromkeys(removable) if t and '"' not in t and "\\" not in t]
        # Empty enum is invalid JSON Schema: maxItems=0 expresses the empty list.
        remove_item = string(enum=names) if names else string()
        return OutputContract(name, obj({"remove": array(remove_item, hi=m.MAX_REMOVE if names else 0),
                                         "add": array(string(1, 40, TAG), hi=m.MAX_ADD),
                                         "sentence": string(10, m.MAX_SENTENCE, SENTENCE)}))
    if module_name == "core.assist_candidates":
        n = args[0] if args else kwargs["n"]
        return OutputContract(name, obj({"choice": {"type": "integer", "enum": list(range(n + 1))}}),
                              lambda value: str(value["choice"]))
    if module_name == "core.assist_recover":
        units = list(args[0] if args else kwargs["units"])
        props = {f"unit_{i + 1}": array(string(1, 15, r"[a-z]+"), 3, 6) for i in range(len(units))}
        for i, unit in enumerate(units):
            props[f"unit_{i + 1}"]["description"] = f"English keywords for Korean unit: {unit}"
        return OutputContract(name, obj(props), lambda v: "\n".join(
            f"{unit} : " + ", ".join(v[f"unit_{i + 1}"]) for i, unit in enumerate(units)))
    if module_name == "core.assist_compose":
        if function == "guess_grammar":
            n = args[0] if args else kwargs["n"]
            # One named property per clause avoids unsupported tuple/prefixItems.
            props = {f"clause_{i + 1}": array(string(1, 40, r'[^"\\\x7f\x00-\x1f]*'), 1, m.MAX_GUESSES)
                     for i in range(n)}
        else:
            cands = args[0] if args else kwargs["cands"]
            props = {f"clause_{i + 1}": string(enum=[*names, "none"]) for i, names in enumerate(cands)}
        keys = list(props)
        return OutputContract(name, obj(props), lambda value: compact([value[k] for k in keys]))
    raise SchemaError("Unsupported Assist output contract")


GRAMMAR_FACTORIES = {
    "core.assist_v2": ("compact_grammar",),
    "core.assist_translate": ("literal_grammar",),
    "core.assist_direct": ("situation_grammar", "direct_grammar"),
    "core.assist_refine": ("refine_grammar",),
    "core.assist_followup": ("followup_grammar",),
    "core.assist_candidates": ("choose_grammar",),
    "core.assist_recover": ("kw_grammar",),
    "core.assist_compose": ("guess_grammar", "choose_grammar"),
}


def wrap_grammar(module_name, function, original):
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        # Followup accepts an iterable: materialize once for grammar AND schema.
        if module_name == "core.assist_followup":
            if args:
                args = (list(args[0]), *args[1:])
            elif "removable" in kwargs:
                kwargs = {**kwargs, "removable": list(kwargs["removable"])}
        grammar = original(*args, **kwargs)
        return SchemaGrammar(grammar, grammar_contract(module_name, function, *args, **kwargs))
    return wrapped
