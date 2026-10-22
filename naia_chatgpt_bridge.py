"""Reversible ChatGPT backend for the unmodified NAIA 2.0.48 Assist/Boost stack.

Local fallback calls the exact original functions. Cloud output contracts wrap
all ten upstream grammar factories, with no grammar forwarded to the provider.
Local engine/model readiness remains false: cloud_ready is a separate capability.
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import inspect
import threading
import time
import types
from pathlib import Path
from typing import Any

if __package__:
    from .naia_chatgpt_schema import GRAMMAR_FACTORIES, SchemaError, SchemaGrammar, wrap_grammar
else:  # Direct host tests; the extension loader uses package-relative imports.
    from naia_chatgpt_schema import GRAMMAR_FACTORIES, SchemaError, SchemaGrammar, wrap_grammar


class BridgeCompatibilityError(RuntimeError):
    pass


# Populated from the audited release; fail closed before any patch on updates.
SUPPORTED_SOURCE_SHA256 = {'core.assist_v2': 'f69839c4de9d51cc1ba36ad10778c6f99952be12e157e1a36ee3bf4f082bba3f', 'core.assist_translate': '4fb9269f22882b35b43e02a232ffbcca6ffc2f4ee87ff7511ffc4ac95ae7b4c8', 'core.assist_direct': 'e400ea735fa675ad47294a2880c4fd7c4163d59535aec69c3deb023ed828d123', 'core.assist_refine': '86a7e5591e9c78a640803af4e3956d9eaadb46fa89e9d6f619cb4cb792c797bc', 'core.assist_followup': '3a005302ab5255cf89e946f6273eb5b38baec27fd01e229dcc9787afabfd8d80', 'core.assist_candidates': 'b4f325787d2666225884bc961d0d47c0da3f9b9276a253be521ac12117b594ce', 'core.assist_recover': 'ede648bd9232122540f3fec6999ef2006424c06b05ceeef729f2eac02b4561af', 'core.assist_compose': 'e0f0f661479941b9ed9d4190f67a5c18a4d11c5f54d28dbd301d449cd41c25f8', 'app.backend.server.boost_v2_service': '236d0c53ee56bf39a8574a25bc599a7a92d8537f0afc61022ab5409b6c08f550', 'app.backend.server.assist_v2_service': 'eb5eba869e5ffb1d3a617ac07259f610132b5c88bc6d5624bd6609bbddcdb27d'}
_INSTALL_LOCK = threading.RLock()
_ACTIVE_CLEANUP = None


def verify_compatibility():
    modules = {}
    for name, expected in SUPPORTED_SOURCE_SHA256.items():
        module = importlib.import_module(name)
        source = Path(inspect.getsourcefile(module) or "")
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise BridgeCompatibilityError(
                f"ChatGPT Connect supports the audited NAIA 2.0.48 only; incompatible module: {name}")
        modules[name] = module
    return modules


class ChatGPTRuntime:
    """LlamaServerRuntime's call seam, without local process/hardware claims."""
    def __init__(self, auth, client, settings_getter):
        self.auth, self.client, self.settings_getter = auth, client, settings_getter
        self._lock = threading.RLock()
        self._leases = {}
        self.last_load_seconds = 0.0

    def status(self):
        settings = self.settings_getter()
        try:
            auth_status = self.auth.status()
            authenticated = bool(auth_status.get("connected"))
            plan_enabled = bool(auth_status.get("plan_enabled"))
        except Exception:
            authenticated = False
            plan_enabled = False
        model = str(settings.get("model") or "").strip()
        enabled = bool(settings.get("enabled"))
        ready = enabled and authenticated and plan_enabled and bool(model)
        with self._lock:
            now = time.monotonic()
            self._leases = {k: until for k, until in self._leases.items() if until is None or until > now}
            leases = {k: None if until is None else round(until - now, 1) for k, until in self._leases.items()}
        error_code = "" if ready else ("backend_disabled" if not enabled else
                                       "auth_required" if not authenticated else
                                       "scope_required" if not plan_enabled else "model_required")
        errors = {"backend_disabled": "ChatGPT Connect is disabled.",
                  "auth_required": "Sign in to ChatGPT in ChatGPT Connect.",
                  "scope_required": "Enable ChatGPT plan access in ChatGPT Connect.",
                  "model_required": "Choose an available ChatGPT model in ChatGPT Connect."}
        return {"backend": "chatgpt", "cloud": True, "local_required": False, "cloud_ready": ready,
                "authenticated": authenticated, "plan_enabled": plan_enabled, "model_id": model, "model_label": model,
                "engine_exists": False, "model_exists": False, "engine_ready": False,
                "engine_installed": False, "model_ready": False, "engine_path": "", "model_path": "",
                "running": False, "leases": leases, "stale": False, "unload_pending": False,
                "use_gpu": False, "device": None, "gpu_failed": None,
                "error_code": error_code, "error": errors.get(error_code, "")}

    def hold(self, owner, seconds=None):
        with self._lock:
            self._leases[str(owner)] = None if seconds is None else time.monotonic() + max(0, seconds)

    def release(self, owner):
        with self._lock:
            self._leases.pop(str(owner), None)
        return False

    def unload(self):
        with self._lock:
            self._leases.clear()
        return False

    stop = unload

    def is_running(self):
        return False

    def warm(self, timeout=120):
        # Cloud has no local process or shader cache. Never spend a model request
        # merely because the user opens Assist or toggles Auto Boost.
        return False

    def chat(self, prompt, *, system=None, grammar=None, max_tokens=512, timeout=120):
        started = time.monotonic()
        status = self.status()
        if not status["cloud_ready"]:
            return {"ok": False, "text": "", "error": status["error"],
                    "error_code": status["error_code"], "elapsed": 0.0}
        contract = None
        if grammar is not None:
            if not isinstance(grammar, SchemaGrammar):
                return {"ok": False, "text": "", "error": "Unsupported NAIA output grammar; update the bridge.",
                        "error_code": "unsupported_grammar", "elapsed": 0.0}
            contract = grammar.contract
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if contract:
            messages.append({"role": "system", "content":
                "Return only the JSON object required by the output schema. "
                "For numbered clause_N and unit_N fields, follow the corresponding input order. "
                "Use the schema shape even if an example shows plain text, a number, or an array."})
        messages.append({"role": "user", "content": str(prompt)})
        try:
            # Local llama token/sampling/context/GPU parameters deliberately do not
            # become Responses fields. timeout is a transport deadline, not a provider field.
            response = dict(self.client.chat(messages, status["model_id"],
                                             json_schema=contract.schema if contract else None, timeout=timeout))
            if response.get("ok") and contract:
                response["text"] = contract.completed_text(response.get("text", ""))
            if not response.get("ok"):
                response["text"] = ""
            response.setdefault("elapsed", round(time.monotonic() - started, 3))
            response.update(backend="chatgpt", cloud=True, load_seconds=0.0, queue_wait=0.0, gpu=False)
            return response
        except SchemaError:
            return {"ok": False, "text": "", "error": "ChatGPT returned output outside the required NAIA schema.",
                    "error_code": "invalid_structured_output", "elapsed": round(time.monotonic() - started, 3)}
        except Exception:
            # Exceptions may contain HTTP headers/tokens/prompts; never surface them.
            return {"ok": False, "text": "", "error": "ChatGPT request failed. Check the connection and try again.",
                    "error_code": "chatgpt_request_failed", "elapsed": round(time.monotonic() - started, 3)}


def _info(response):
    info = {key: response[key] for key in ("elapsed", "usage", "load_seconds", "queue_wait", "gpu", "backend")
            if key in response}
    if not response.get("ok"):
        info.update(error=response.get("error") or "ChatGPT request failed.",
                    code=response.get("error_code") or "chatgpt_request_failed")
    return info


def install_bridge(auth, client, settings_getter, *, app=None):
    """Install once; return cleanup. Optional FastAPI app supports late activation.

    settings_getter returns {enabled: bool, model: str}. The caller owns durable
    settings, extension trust/activation, authentication, and model discovery.
    """
    global _ACTIVE_CLEANUP
    with _INSTALL_LOCK:
        if _ACTIVE_CLEANUP is not None:
            raise BridgeCompatibilityError("ChatGPT bridge is already installed")
        modules = verify_compatibility()
        boost = modules["app.backend.server.boost_v2_service"]
        assist = modules["app.backend.server.assist_v2_service"]
        patches, cells, contexts, dispatches = [], [], [], []
        old_cache = assist._GRAMMAR

        def enabled():
            return bool(settings_getter().get("enabled"))

        def patch(module, name, replacement):
            original = getattr(module, name)
            patches.append((module, name, original, replacement))
            setattr(module, name, replacement)
            return original

        def original_copy(fn):
            if fn.__closure__:
                raise BridgeCompatibilityError(f"Unexpected closure in {fn.__name__}")
            clone = types.FunctionType(fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__)
            clone.__kwdefaults__ = fn.__kwdefaults__
            clone.__annotations__ = fn.__annotations__
            return clone

        def dispatch(module, name, replacement):
            # Route registration happens before extension loading. Keep the exact
            # captured function identity and install a version-bound dispatcher.
            # Originals above were copied BEFORE this mutation for local fallback.
            original = getattr(module, name)
            if original.__closure__ or original.__module__ != module.__name__:
                raise BridgeCompatibilityError(f"Unexpected captured function: {name}")
            key = "__naia_chatgpt_bridge_dispatch_" + name
            if key in module.__dict__:
                raise BridgeCompatibilityError("Another ChatGPT dispatcher is installed")
            scope = {}
            exec(compile(f"def dispatch(*args, **kwargs):\n    return {key}(*args, **kwargs)\n",
                         "<naia-chatgpt-dispatch>", "exec"), scope)
            code = scope["dispatch"].__code__
            saved = (original.__code__, original.__defaults__, original.__kwdefaults__)
            module.__dict__[key] = replacement
            original.__code__, original.__defaults__, original.__kwdefaults__ = code, None, None
            dispatches.append((module, key, original, saved, code, replacement))

        def runtime(context):
            with _INSTALL_LOCK:
                rt = getattr(context, "chatgpt_boost_runtime", None)
                if rt is None:
                    rt = ChatGPTRuntime(auth, client, settings_getter)
                    context.chatgpt_boost_runtime = rt
                    contexts.append((context, rt))
                return rt

        original_runtime = original_copy(boost.get_boost_runtime)
        @functools.wraps(original_runtime)
        def get_runtime(context, settings=None):
            return runtime(context) if enabled() else original_runtime(context, settings)

        original_chat = assist._chat
        @functools.wraps(original_chat)
        def chat(context, system, user, grammar, max_tokens):
            if not enabled():
                return original_chat(context, system, user, grammar, max_tokens)
            rt = runtime(context)
            rt.hold("assist", assist.ASSIST_LEASE_SECONDS)
            response = rt.chat(user, system=system, grammar=grammar, max_tokens=max_tokens,
                               timeout=assist.MODEL_TIMEOUT)
            return (response.get("text") if response.get("ok") else None), _info(response)

        original_call = assist._call_model
        @functools.wraps(original_call)
        def call_model(context, text, previous, literal=None):
            if not enabled():
                return original_call(context, text, previous, literal)
            from core.assist_v2 import SYSTEM_PROMPT, parse_route, user_message
            reply, info = chat(context, SYSTEM_PROMPT, user_message(text, previous, literal),
                               assist._grammar(), assist.MODEL_MAX_TOKENS)
            if reply is None:
                return None, info
            try:
                return parse_route(reply), info
            except Exception:
                return None, {**info, "error": "ChatGPT returned an invalid NAIA route.", "code": "invalid_route"}

        original_assist_status = original_copy(assist.assist_status)
        @functools.wraps(original_assist_status)
        def assist_status(context):
            status = original_assist_status(context)
            return {**status, **runtime(context).status()} if enabled() else status

        original_boost_status = original_copy(boost.boost_v2_status)
        @functools.wraps(original_boost_status)
        def boost_status(context):
            if not enabled():
                return original_boost_status(context)
            # Avoid even probing a local engine, model downloader or GPU for cloud.
            return {"ok": True, "settings": boost.boost_v2_settings(context),
                    "models": [], "mode": "cloud", "gpu_available": False,
                    "gpu_devices": [], "gpu_device_entries": [], "gpu_device_chosen": None,
                    "hardware": {}, "prerequisites": {"ok": True, "required": False},
                    "download": {"active": False}, "engine_install": {"active": False},
                    "priming": False, "swapping": False, "last_load_seconds": None,
                    **runtime(context).status()}

        original_release = original_copy(boost.release_boost_runtime)
        @functools.wraps(original_release)
        def release(context):
            if enabled():
                runtime(context).release("boost")
                return None
            return original_release(context)

        original_stop = original_copy(boost.stop_boost_runtime)
        @functools.wraps(original_stop)
        def stop(context):
            if enabled():
                runtime(context).unload()
                return {"unloaded": False, "pending": False, "cloud": True, "local_required": False}
            return original_stop(context)

        def cleanup():
            global _ACTIVE_CLEANUP
            with _INSTALL_LOCK:
                for module, key, original, saved, code, replacement in reversed(dispatches):
                    if original.__code__ is code:
                        original.__code__, original.__defaults__, original.__kwdefaults__ = saved
                    if module.__dict__.get(key) is replacement:
                        del module.__dict__[key]
                for cell, original, replacement in reversed(cells):
                    if cell.cell_contents is replacement:
                        cell.cell_contents = original
                for module, name, original, replacement in reversed(patches):
                    if getattr(module, name) is replacement:
                        setattr(module, name, original)
                if isinstance(assist._GRAMMAR, SchemaGrammar) or assist._GRAMMAR is None:
                    assist._GRAMMAR = old_cache
                for context, rt in contexts:
                    rt.unload()
                    if getattr(context, "chatgpt_boost_runtime", None) is rt:
                        del context.chatgpt_boost_runtime
                if _ACTIVE_CLEANUP is cleanup:
                    _ACTIVE_CLEANUP = None

        try:
            for module_name, names in GRAMMAR_FACTORIES.items():
                for name in names:
                    module = modules[module_name]
                    patch(module, name, wrap_grammar(module_name, name, getattr(module, name)))
            assist._GRAMMAR = None  # Cached pre-install GBNF must acquire its schema.
            for module, name, replacement in (
                (boost, "get_boost_runtime", get_runtime), (boost, "boost_v2_status", boost_status),
                (boost, "release_boost_runtime", release), (boost, "stop_boost_runtime", stop),
                (assist, "_chat", chat), (assist, "_call_model", call_model),
                (assist, "assist_status", assist_status),
            ):
                if name in {"get_boost_runtime", "boost_v2_status", "release_boost_runtime",
                            "stop_boost_runtime", "assist_status"}:
                    dispatch(module, name, replacement)
                else:
                    patch(module, name, replacement)
            if app is not None:
                # Existing FastAPI route closures captured imports when registered.
                # Replace only known callable identities in NAIA Assist/Boost routes;
                # preserve route guards, endpoint functions and dependency wiring.
                replacements = {id(old): (old, new) for _, _, old, new in patches}
                seen = set()
                def refresh(fn):
                    if not inspect.isfunction(fn) or id(fn) in seen:
                        return
                    seen.add(id(fn))
                    if not fn.__module__.startswith("app.backend.server."):
                        return
                    for cell in fn.__closure__ or ():
                        try:
                            value = cell.cell_contents
                        except ValueError:
                            continue
                        pair = replacements.get(id(value))
                        if pair is not None and value is pair[0]:
                            cell.cell_contents = pair[1]
                            cells.append((cell, *pair))
                        elif inspect.isfunction(value):
                            refresh(value)
                for route in app.routes:
                    if str(getattr(route, "path", "")).startswith(("/api/boost-v2/", "/api/assist/")):
                        refresh(getattr(route, "endpoint", None))
            _ACTIVE_CLEANUP = cleanup
            return cleanup
        except Exception:
            cleanup()
            raise
