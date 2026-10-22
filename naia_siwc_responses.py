"""Bounded, non-retrying Responses transport for ChatGPT plan usage.

Only OAuth bearer credentials from AuthManager are accepted. No API-key or
backend-api fallback exists. Text is released only from response.completed.
Protocol: developers.openai.com/siwc/token-sharing-open-source/models-and-inference
"""
from __future__ import annotations

import json
import math
import socket
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.openai.com/v1"
MAX_BODY = 2 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_STREAM = 8 * 1024 * 1024

ERRORS = {
    "authentication_required": "ChatGPT 로그인이 필요합니다. 계정과 사용 권한을 확인하세요.",
    "permission_denied": "ChatGPT 계정 또는 지역 정책으로 요청이 허용되지 않았습니다.",
    "rate_limit": "사용 한도에 도달했습니다. ChatGPT 설정의 사용량을 확인하세요.",
    "service_unavailable": "ChatGPT 서비스를 일시적으로 사용할 수 없습니다. 나중에 다시 시도하세요.",
    "invalid_request": "ChatGPT가 요청을 지원하지 않습니다. 모델과 출력 형식을 확인하세요.",
    "response_failed": "ChatGPT 요청이 실패했습니다. 결과를 적용하지 않았습니다.",
    "malformed_response": "ChatGPT 응답 형식이 올바르지 않아 결과를 적용하지 않았습니다.",
    "incomplete_response": "ChatGPT 응답이 완료되지 않아 결과를 적용하지 않았습니다.",
    "refusal": "ChatGPT가 이 요청에 대한 응답을 거절했습니다.",
    "timeout": "ChatGPT 응답 제한시간을 초과했습니다. 요청을 자동 재전송하지 않았습니다.",
    "network_error": "ChatGPT 연결이 끊겼습니다. 요청을 자동 재전송하지 않았습니다.",
    "response_too_large": "ChatGPT 응답 크기 제한을 초과했습니다.",
    "subscription_sharing_user_not_eligible": "선택한 계정 또는 워크스페이스에서 ChatGPT 요금제 사용이 허용되지 않습니다.",
    "subscription_sharing_usage_limit_exceeded": "ChatGPT 요금제 사용 한도에 도달했습니다. ChatGPT 설정의 사용량을 확인하세요.",
    "subscription_sharing_usage_unavailable": "ChatGPT 사용량을 확인할 수 없습니다. 나중에 다시 시도하세요.",
    "subscription_sharing_unsupported_capability": "선택한 모델 또는 요청 기능을 ChatGPT 요금제에서 지원하지 않습니다.",
    "subscription_sharing_route_not_supported": "ChatGPT 요금제 요청 경로가 허용되지 않았습니다.",
    "subscription_sharing_invalid_user": "ChatGPT 계정 인증을 확인할 수 없습니다. 계정과 로그인 상태를 확인하세요.",
    "subscription_sharing_user_unavailable": "ChatGPT 계정 정보를 일시적으로 확인할 수 없습니다.",
    "chatpass_v2_scope_not_authorized": "ChatGPT 사용 권한이 없습니다. 앱의 로그인 권한 설정을 확인하세요.",
    "chatpass_v2_invalid_authorization_context": "ChatGPT 인증 권한을 확인할 수 없습니다. 앱의 로그인 설정을 확인하세요.",
}


class ResponsesError(RuntimeError):
    """Only fixed, non-sensitive messages escape the transport."""
    def __init__(self, code, status=None):
        self.code = code if code in ERRORS else "response_failed"
        self.status = status
        super().__init__(ERRORS[self.code])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib can forward Authorization across redirects. Never follow one.
        raise ResponsesError("permission_denied", code)


def _safe_code(value, default="response_failed"):
    return value if isinstance(value, str) and value in ERRORS else default


def _http_error(exc):
    default = {400: "invalid_request", 401: "authentication_required",
               403: "permission_denied", 429: "rate_limit"}.get(exc.code, "service_unavailable")
    code = default
    try:
        data = json.loads(exc.read(16384))
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            code = _safe_code(data["error"].get("code"), default)
    except Exception:
        pass
    finally:
        exc.close()
    return ResponsesError(code, exc.code)


def _events(response, deadline):
    """SSE frames with bounded memory; EOF never supplies a terminal event."""
    data, event_name, total, frame_size = [], None, 0, 0
    while True:
        if time.monotonic() >= deadline:
            raise ResponsesError("timeout")
        line = response.readline(MAX_LINE + 1)
        if time.monotonic() >= deadline:
            raise ResponsesError("timeout")
        if not line:
            raise ResponsesError("incomplete_response")
        total += len(line)
        frame_size += len(line)
        if len(line) > MAX_LINE or total > MAX_STREAM or frame_size > MAX_BODY:
            raise ResponsesError("response_too_large")
        try:
            line = line.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise ResponsesError("malformed_response") from None
        if not line:
            if data:
                try:
                    event = json.loads("\n".join(data))
                except (ValueError, RecursionError):
                    raise ResponsesError("malformed_response") from None
                if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
                        or (event_name and event_name != event["type"])):
                    raise ResponsesError("malformed_response")
                yield event
            data, event_name, frame_size = [], None, 0
        elif line.startswith(":"):
            continue
        else:
            key, sep, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if key == "data" and sep:
                data.append(value)
            elif key == "event" and sep:
                event_name = value
            elif key not in ("id", "retry"):
                raise ResponsesError("malformed_response")


def _completed(response):
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise ResponsesError("incomplete_response")
    if response.get("error") or response.get("incomplete_details"):
        raise ResponsesError("response_failed")
    output = response.get("output")
    if not isinstance(output, list):
        raise ResponsesError("malformed_response")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ResponsesError("malformed_response")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ResponsesError("malformed_response")
        if item.get("status") != "completed" or not isinstance(item.get("content"), list):
            raise ResponsesError("incomplete_response")
        for part in item["content"]:
            if not isinstance(part, dict):
                raise ResponsesError("malformed_response")
            if part.get("type") == "refusal":
                raise ResponsesError("refusal")
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ResponsesError("malformed_response")
            texts.append(part["text"])
    text = "".join(texts)
    if not text.strip():
        raise ResponsesError("incomplete_response")
    # Do not return arbitrary server fields (which could contain diagnostic data).
    source = response.get("usage") or {}
    if not isinstance(source, dict):
        raise ResponsesError("malformed_response")
    usage = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = source.get(key)
        if value is not None:
            if type(value) is not int or value < 0:
                raise ResponsesError("malformed_response")
            usage[key] = value
    if "input_tokens" in usage:
        usage["prompt_tokens"] = usage["input_tokens"]
    if "output_tokens" in usage:
        usage["completion_tokens"] = usage["output_tokens"]
    return text, usage


class ResponsesClient:
    def __init__(self, auth_manager, *, opener=None, timeout=120):
        self.auth_manager = auth_manager
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 300:
            raise ValueError("timeout must be between 0 and 300 seconds")

    def _token(self, force_refresh=False):
        try:
            token = self.auth_manager.get_access_token(force_refresh=force_refresh)
        except Exception:
            raise ResponsesError("authentication_required") from None
        if (not isinstance(token, str) or not token or token.startswith("sk-")
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ResponsesError("authentication_required")
        return token

    def _open(self, path, token, body=None, timeout=None):
        request = urllib.request.Request(
            API_ROOT + path, data=body,
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json",
                     "Accept": "text/event-stream" if body is not None else "application/json"},
            method="POST" if body is not None else "GET")
        return self.opener.open(request, timeout=min(timeout or self.timeout, 30.0))

    def list_models(self):
        """Return displayable account-specific models, preserving server order."""
        for attempt in range(2):
            token = self._token(force_refresh=bool(attempt))
            try:
                with self._open("/models", token) as response:
                    raw = response.read(MAX_BODY + 1)
                    if len(raw) > MAX_BODY:
                        raise ResponsesError("response_too_large")
                    data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                    raise ResponsesError("malformed_response")
                models = []
                for model in data["models"]:
                    if not isinstance(model, dict):
                        raise ResponsesError("malformed_response")
                    if model.get("visibility") != "list":
                        continue
                    if not all(isinstance(model.get(k), str) and model[k].strip()
                               for k in ("slug", "display_name")):
                        raise ResponsesError("malformed_response")
                    models.append({"slug": model["slug"], "display_name": model["display_name"]})
                return models
            except urllib.error.HTTPError as exc:
                error = _http_error(exc)
                if error.status == 401 and attempt == 0:
                    continue  # GET is safe to repeat after one token refresh.
                raise error from None
            except ResponsesError:
                raise
            except (TimeoutError, socket.timeout):
                raise ResponsesError("timeout") from None
            except (ValueError, UnicodeError, RecursionError):
                raise ResponsesError("malformed_response") from None
            except Exception:
                raise ResponsesError("network_error") from None

    def chat(self, messages, model, json_schema=None, *, timeout=None, **ignoredlocalparams):
        """No automatic POST retries; failures always return empty text."""
        started = time.monotonic()
        try:
            duration = self.timeout if timeout is None else float(timeout)
            if not math.isfinite(duration) or not 0 < duration <= 300:
                raise ResponsesError("invalid_request")
            if not isinstance(model, str) or not model.strip() or len(model) > 256:
                raise ResponsesError("invalid_request")
            if not isinstance(messages, list) or not messages:
                raise ResponsesError("invalid_request")
            normalized = []
            for message in messages:
                if not isinstance(message, dict):
                    raise ResponsesError("invalid_request")
                role, content = message.get("role"), message.get("content")
                if role not in ("system", "developer", "user", "assistant") or not isinstance(content, str):
                    raise ResponsesError("invalid_request")
                normalized.append({"role": "developer" if role == "system" else role, "content": content})
            body = {"model": model, "input": normalized, "store": False, "stream": True}
            if json_schema is not None:
                if not isinstance(json_schema, dict) or json_schema.get("type") != "object":
                    raise ResponsesError("invalid_request")
                body["text"] = {"format": {"type": "json_schema", "name": "naia_response",
                                           "strict": True, "schema": json_schema}}
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_BODY:
                raise ResponsesError("invalid_request")
            token = self._token()
            deadline = time.monotonic() + duration
            with self._open("/responses", token, encoded, duration) as response:
                if response.headers.get("Content-Type", "").split(";", 1)[0].strip() != "text/event-stream":
                    raise ResponsesError("malformed_response")
                sequence, response_id = -1, None
                for event in _events(response, deadline):
                    number = event.get("sequence_number")
                    if number is not None:
                        if type(number) is not int or number <= sequence:
                            raise ResponsesError("malformed_response")
                        sequence = number
                    kind = event["type"]
                    if kind.startswith("response.refusal."):
                        raise ResponsesError("refusal")
                    if kind in ("error", "response.failed"):
                        failed = event.get("response")
                        if kind == "response.failed" and not isinstance(failed, dict):
                            raise ResponsesError("malformed_response")
                        error = failed.get("error", {}) if kind == "response.failed" else event
                        raise ResponsesError(_safe_code(error.get("code") if isinstance(error, dict) else None))
                    if kind in ("response.incomplete", "response.cancelled"):
                        raise ResponsesError("incomplete_response")
                    if kind == "response.created":
                        obj = event.get("response")
                        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
                            raise ResponsesError("malformed_response")
                        response_id = obj["id"]
                    if kind == "response.output_text.delta" and not isinstance(event.get("delta"), str):
                        raise ResponsesError("malformed_response")
                    if kind == "response.completed":
                        obj = event.get("response")
                        if response_id and (not isinstance(obj, dict) or obj.get("id") != response_id):
                            raise ResponsesError("malformed_response")
                        text, usage = _completed(obj)
                        if json_schema is not None:
                            try:
                                parsed = json.loads(text)
                            except (ValueError, RecursionError):
                                raise ResponsesError("malformed_response") from None
                            if not isinstance(parsed, dict):
                                raise ResponsesError("malformed_response")
                        return {"ok": True, "text": text, "usage": usage, "finish_reason": "stop",
                                "elapsed": round(time.monotonic() - started, 2), "error": None}
        except urllib.error.HTTPError as exc:
            error = _http_error(exc)
            # A 401 never repeats generation. Refresh before a later user request.
        except ResponsesError as exc:
            error = exc
        except (TimeoutError, socket.timeout):
            error = ResponsesError("timeout")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            error = ResponsesError("invalid_request")
        except Exception:
            error = ResponsesError("network_error")
        return {"ok": False, "text": "", "usage": {}, "elapsed": round(time.monotonic() - started, 2),
                "error": str(error), "error_code": error.code, "http_status": error.status}
