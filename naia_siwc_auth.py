"""Public-client Sign in with ChatGPT. No inference and no API-key fallback.

The caller MUST supply an app-private data directory, never shared storage.
Credentials are permission-protected, not encrypted at rest by this module.
Only this backend may call get_access_token(); never return it to the web UI.
Protocol: developers.openai.com/siwc/token-sharing-open-source/sign-in
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import stat
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ISSUER = "https://auth.openai.com"
DISCOVERY_URL = ISSUER + "/.well-known/openid-configuration"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
TERMINAL_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired",
                    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}


class AuthError(Exception):
    """Safe, fixed messages; never carry response bodies, URLs or credentials."""
    def __init__(self, message, code="auth_error"):
        super().__init__(message)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    """Inject request(method, url, form=None) -> (HTTP status, JSON object)."""
    def request(self, method, url, form=None):
        # Do not forward refresh tokens to redirect destinations or ambient proxies.
        opener = build_opener(_NoRedirect(), ProxyHandler({}))
        data = None if form is None else urlencode(form).encode("ascii")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            try:
                response = opener.open(request, timeout=20)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise AuthError("Authentication response was too large.", "invalid_response")
                value = json.loads(raw) if raw else {}
                if not isinstance(value, dict):
                    raise ValueError()
                return response.code, value
        except AuthError:
            raise
        except (URLError, OSError, ValueError):
            raise AuthError("Cannot reach ChatGPT authentication. Try again later.", "network_error") from None


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(value):
    if not isinstance(value, str) or len(value) > 131072:
        raise ValueError()
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class AuthManager:
    def __init__(self, data_dir, *, transport=None, clock=time.time, agent_name="NAIA"):
        self.directory = Path(data_dir).absolute()
        if self.directory.is_symlink():
            raise AuthError("Credential directory must not be a symlink.", "storage_error")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "chatgpt-credentials.json"
        self.transport = transport or HTTPTransport()
        self.clock = clock
        self.agent_name = agent_name
        self._mutex = threading.RLock()
        self._pending = None
        self._server = None
        self._timer = None
        self._last_error = None
        self._retry_client = None
        self._jwks = None
        self._jwks_at = 0
        with self._locked():
            if not self.path.exists():
                self._save({"version": 1, "ext_agent_host_id": "urn:uuid:" + str(uuid.uuid4()),
                            "active": None, "accounts": {}})
            self._load()

    @contextlib.contextmanager
    def _locked(self):
        with self._mutex:
            fd = os.open(self.directory / "credentials.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError()
                os.fchmod(stream.fileno(), 0o600)
                data = json.load(stream)
            if (data.get("version") != 1 or not isinstance(data.get("accounts"), dict)
                    or not data.get("ext_agent_host_id", "").startswith("urn:uuid:")):
                raise ValueError()
            return data
        except (OSError, ValueError, AttributeError):
            raise AuthError("Private ChatGPT credential storage cannot be read.", "storage_error") from None

    def _save(self, data):
        name = None
        try:
            fd, name = tempfile.mkstemp(prefix=".credentials-", dir=self.directory)
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise AuthError("Private ChatGPT credentials could not be saved.", "storage_error") from None
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def _request(self, method, url, form=None):
        try:
            return self.transport.request(method, url, form)
        except AuthError:
            raise
        except Exception:
            raise AuthError("ChatGPT authentication request failed. Try again later.", "network_error") from None

    def _discovery(self):
        status, data = self._request("GET", DISCOVERY_URL)
        expected = {"issuer": ISSUER, "authorization_endpoint": AUTHORIZE_URL,
                    "token_endpoint": TOKEN_URL, "jwks_uri": JWKS_URL}
        if status != 200 or any(data.get(key) != value for key, value in expected.items()):
            raise AuthError("ChatGPT authentication configuration could not be verified.", "discovery_error")
        return data

    def _verify_id_token(self, token, client_id, nonce=None):
        try:
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
            from cryptography.hazmat.primitives import hashes
        except ImportError:
            raise AuthError("앱에 필요한 인증 모듈이 없습니다. 호환 업데이트가 필요합니다.", "missing_dependency") from None
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError()
            header, claims = (json.loads(_decode(part)) for part in parts[:2])
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or header.get("crit"):
                raise ValueError()
            keys = self._jwks if self.clock() - self._jwks_at < 3600 else None
            matches = [] if keys is None else [k for k in keys if k.get("kid") == header["kid"]]
            if not matches:
                self._discovery()
                status, body = self._request("GET", JWKS_URL)
                if status != 200 or not isinstance(body.get("keys"), list):
                    raise ValueError()
                self._jwks, self._jwks_at = body["keys"], self.clock()
                matches = [k for k in self._jwks if k.get("kid") == header["kid"]]
            if len(matches) != 1:
                raise ValueError()
            key = matches[0]
            if (key.get("kty") != "RSA" or key.get("use", "sig") != "sig"
                    or key.get("alg", "RS256") != "RS256"
                    or "verify" not in key.get("key_ops", ["verify"])):
                raise ValueError()
            public = rsa.RSAPublicNumbers(int.from_bytes(_decode(key["e"]), "big"),
                                          int.from_bytes(_decode(key["n"]), "big")).public_key()
            if public.key_size < 2048:
                raise ValueError()
            public.verify(_decode(parts[2]), (parts[0] + "." + parts[1]).encode("ascii"),
                          padding.PKCS1v15(), hashes.SHA256())
            audiences = claims.get("aud")
            audiences = [audiences] if isinstance(audiences, str) else audiences
            now = self.clock()
            if (claims.get("iss") != ISSUER or not isinstance(audiences, list) or client_id not in audiences
                    or (len(audiences) > 1 and claims.get("azp") != client_id)
                    or ("azp" in claims and claims["azp"] != client_id)
                    or not isinstance(claims.get("sub"), str) or not claims["sub"]
                    or not _number(claims.get("exp")) or claims["exp"] <= now - 5
                    or not _number(claims.get("iat")) or claims["iat"] > now + 5
                    or ("nbf" in claims and (not _number(claims["nbf"]) or claims["nbf"] > now + 5))
                    or (nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce))):
                raise ValueError()
            return claims
        except AuthError:
            raise
        except Exception:
            raise AuthError("ChatGPT identity could not be verified. Start sign-in again.", "invalid_identity") from None

    def _stop_listener(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        server, self._server = self._server, None
        if server:
            # shutdown may wait for the serve loop; never join a callback worker here.
            server.shutdown()
            server.server_close()

    def begin_login(self, account_id=None, *, new_account=False, consent=False):
        with self._locked():
            try:
                import cryptography  # noqa: F401
            except ImportError:
                raise AuthError("앱에 필요한 인증 모듈이 없습니다. 호환 업데이트가 필요합니다.", "missing_dependency") from None
            self._stop_listener()
            self._pending = None
            data = self._load()
            selected = None if new_account else (account_id or data["active"])
            # A failed new registration must retry its issued client, even while
            # another validated account remains active in the application.
            retry_client = self._retry_client if account_id is None else None
            if retry_client:
                selected = next((key for key, value in data["accounts"].items()
                                 if value["client_id"] == retry_client), None)
            account = data["accounts"].get(selected) if selected else None
            if selected and not account:
                raise AuthError("Choose a saved ChatGPT account.", "unknown_account")
            client = account["client_id"] if account else retry_client
            manager = self

            class CallbackHandler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass  # Default HTTP logging exposes authorization codes.

                def do_GET(self):
                    expected_host = "127.0.0.1:" + str(self.server.server_port)
                    if self.headers.get("Host") != expected_host or urlsplit(self.path).path != "/auth/callback":
                        self.send_error(400, "Invalid callback")
                        return
                    try:
                        manager.consume_callback(self.path)
                        message, status = "ChatGPT connected. You may close this tab and return to NAIA.", 200
                    except AuthError:
                        message, status = "Sign-in could not be completed. Return to NAIA to try again.", 400
                    body = message.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(body)

            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
                self._server.daemon_threads = True
            except OSError:
                raise AuthError("Could not start the local ChatGPT callback listener.", "callback_unavailable") from None
            redirect = "http://127.0.0.1:%d/auth/callback" % self._server.server_port
            pending = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
                       "verifier": secrets.token_urlsafe(64), "redirect_uri": redirect,
                       "expires_at": self.clock() + 600, "account_id": selected,
                       "client_id": client, "subject": account.get("subject") if account else None}
            self._pending = pending
            self._last_error = None
            query = {"client_id": client or "dynamic_agent_client", "ext_agent_host_id": data["ext_agent_host_id"],
                     "redirect_uri": redirect, "response_type": "code", "scope": SCOPES, "resource": RESOURCE,
                     "state": pending["state"], "nonce": pending["nonce"], "code_challenge_method": "S256",
                     "code_challenge": _b64(hashlib.sha256(pending["verifier"].encode("ascii")).digest())}
            if not client:
                query["agent_name_hint"] = self.agent_name
            if consent:
                query["prompt"] = "consent"
            # Optional id_token_hint deliberately omitted: URLs returned to the UI contain no tokens.
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
            self._timer = threading.Timer(600, self._expire_attempt, args=(pending["state"],))
            self._timer.daemon = True
            self._timer.start()
            return {"authorization_url": AUTHORIZE_URL + "?" + urlencode(query), "expires_in": 600}

    def _expire_attempt(self, state):
        with self._mutex:
            if self._pending and self._pending["state"] == state:
                self.close()

    def consume_callback(self, callback_url):
        with self._locked():
            pending, self._pending = self._pending, None
            try:
                if not pending or pending["expires_at"] <= self.clock():
                    raise AuthError("Sign-in expired. Start again.", "expired_state")
                parsed = urlsplit(callback_url)
                if parsed.path != "/auth/callback" or (parsed.netloc and callback_url.split("?", 1)[0] != pending["redirect_uri"]):
                    raise AuthError("Sign-in callback could not be verified.", "invalid_state")
                values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
                if any(len(v) != 1 for v in values.values()):
                    raise AuthError("Sign-in callback could not be verified.", "invalid_state")
                values = {key: value[0] for key, value in values.items()}
                if not hmac.compare_digest(values.get("state", ""), pending["state"]):
                    raise AuthError("Sign-in callback could not be verified.", "invalid_state")
                if "error" in values:
                    raise AuthError("ChatGPT sign-in was declined or cancelled.", "access_denied")
                client = values.get("client_id") or pending["client_id"]
                if (not client or client == "dynamic_agent_client" or len(client) > 512
                        or (pending["client_id"] and client != pending["client_id"])):
                    raise AuthError("ChatGPT registration was incomplete. Start again.", "invalid_client")
                if not values.get("code"):
                    raise AuthError("Sign-in did not return an authorization code.", "missing_code")
                status, response = self._request("POST", TOKEN_URL, {
                    "grant_type": "authorization_code", "client_id": client, "code": values["code"],
                    "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"], "resource": RESOURCE})
                if status != 200:
                    if response.get("error") == "invalid_grant":
                        self._retry_client = client
                    raise AuthError("ChatGPT code exchange failed. Start sign-in again.", "exchange_failed")
                identity = self._verify_id_token(response.get("id_token", ""), client, pending["nonce"])
                if pending["subject"] and identity["sub"] != pending["subject"]:
                    raise AuthError("ChatGPT returned a different account. Choose Add account instead.", "account_mismatch")
                data = self._load()
                account_id = hashlib.sha256((ISSUER + "\0" + client).encode()).hexdigest()[:24]
                existing = data["accounts"].get(account_id)
                if existing and existing["subject"] != identity["sub"]:
                    raise AuthError("ChatGPT registration identity changed.", "account_mismatch")
                record = {"client_id": client, "issuer": ISSUER, "subject": identity["sub"],
                          "email": identity.get("email") if isinstance(identity.get("email"), str) else "",
                          "label": "Account " + account_id[:8]}
                record.update(self._token_fields(response))
                data["accounts"][account_id] = record
                data["active"] = account_id
                self._save(data)
                self._retry_client = None
                self._last_error = None
                return self._safe_status(data)
            except AuthError as error:
                self._last_error = str(error)
                raise
            except (ValueError, TypeError):
                self._last_error = "Invalid sign-in callback. Start again."
                raise AuthError(self._last_error, "invalid_callback") from None
            finally:
                self._stop_listener()

    def _token_fields(self, response, previous=None):
        access = response.get("access_token")
        refresh = response.get("refresh_token")
        expires = response.get("expires_in")
        scope = response.get("scope")
        token_type = response.get("token_type")
        if (not isinstance(access, str) or not access or len(access) > 131072
                or any(ord(c) < 33 or ord(c) > 126 for c in access)
                or not isinstance(token_type, str) or token_type.lower() != "bearer"
                or not _number(expires) or not 0 < expires <= 86400
                or (scope is not None and not isinstance(scope, str))
                or (refresh is not None and not isinstance(refresh, str))):
            raise AuthError("ChatGPT returned incomplete credentials.", "invalid_response")
        scopes = scope.split() if scope is not None else (previous or {}).get("scopes", [])
        if ("offline_access" in scopes or previous) and (not isinstance(refresh, str) or not refresh):
            raise AuthError("ChatGPT did not return a replacement refresh token. Sign in again.", "invalid_response")
        earliest = response.get("earliest_refresh_at", 0)
        if isinstance(earliest, str):
            try:
                earliest = datetime.fromisoformat(earliest.replace("Z", "+00:00")).timestamp()
            except ValueError:
                raise AuthError("ChatGPT returned an invalid refresh time.", "invalid_response") from None
        if not _number(earliest):
            raise AuthError("ChatGPT returned an invalid refresh time.", "invalid_response")
        fields = {"access_token": access, "refresh_token": refresh or "", "token_type": "Bearer",
                  "scopes": sorted(set(scopes)), "expires_at": self.clock() + expires,
                  "earliest_refresh_at": earliest, "saved_at": self.clock()}
        if response.get("id_token"):
            fields["id_token"] = response["id_token"]
        return fields

    @staticmethod
    def _clear_tokens(record):
        for key in ("access_token", "refresh_token", "id_token", "scopes", "expires_at", "earliest_refresh_at", "saved_at", "token_type"):
            record.pop(key, None)

    def get_access_token(self, force_refresh=False):
        with self._locked():
            data = self._load()
            record = data["accounts"].get(data["active"])
            if not record or not record.get("access_token"):
                raise AuthError("Continue with ChatGPT to connect your account.", "sign_in_required")
            if not {DIRECT_SCOPE, "resource.invoke"}.issubset(record.get("scopes", [])):
                raise AuthError("ChatGPT plan usage is not enabled. Enable it in connection settings.", "scope_required")
            now = self.clock()
            if not force_refresh and record["expires_at"] > now + 60:
                return record["access_token"]
            if now < record.get("earliest_refresh_at", 0):
                if not force_refresh and record["expires_at"] > now + 5:
                    return record["access_token"]
                raise AuthError("ChatGPT access cannot be renewed yet. Try again later.", "refresh_not_ready")
            if not record.get("refresh_token"):
                raise AuthError("ChatGPT access expired. Sign in again.", "sign_in_required")
            status, response = self._request("POST", TOKEN_URL, {
                "grant_type": "refresh_token", "client_id": record["client_id"],
                "refresh_token": record["refresh_token"], "resource": RESOURCE})
            if status != 200:
                code = response.get("error")
                if isinstance(code, dict):
                    code = code.get("code")
                if not isinstance(code, str):
                    code = "unknown_error"
                if code in TERMINAL_REFRESH:
                    self._clear_tokens(record)
                    self._save(data)
                    raise AuthError("ChatGPT session ended. Sign in again.", "sign_in_required")
                if code == "invalid_client":
                    raise AuthError("ChatGPT client registration is invalid. Check the integration.", "invalid_client")
                raise AuthError("ChatGPT access could not be renewed. Try again later.", "refresh_unavailable")
            if response.get("id_token"):
                identity = self._verify_id_token(response["id_token"], record["client_id"])
                if identity["sub"] != record["subject"]:
                    raise AuthError("ChatGPT refresh returned a different account.", "account_mismatch")
            record.update(self._token_fields(response, record))
            self._save(data)
            if not {DIRECT_SCOPE, "resource.invoke"}.issubset(record["scopes"]):
                raise AuthError("ChatGPT plan permission was removed. Enable it in connection settings.", "scope_required")
            return record["access_token"]

    access_token = get_access_token

    def _safe_status(self, data):
        accounts = []
        for key, record in data["accounts"].items():
            accounts.append({"account_id": key, "label": record["label"], "email": record.get("email", ""),
                             "signed_in": bool(record.get("access_token")),
                             "plan_enabled": {DIRECT_SCOPE, "resource.invoke"}.issubset(record.get("scopes", [])),
                             "active": key == data["active"]})
        active = next((a for a in accounts if a["active"]), None)
        return {"connected": bool(active and active["signed_in"]), "plan_enabled": bool(active and active["plan_enabled"]),
                "active_account_id": data["active"], "accounts": accounts,
                "pending": self._pending is not None, "error": self._last_error,
                "storage": "private_file_permissions"}

    def status(self):
        with self._locked():
            return self._safe_status(self._load())

    def select_account(self, account_id):
        with self._locked():
            data = self._load()
            if account_id not in data["accounts"]:
                raise AuthError("Choose a saved ChatGPT account.", "unknown_account")
            self._pending = None
            self._stop_listener()
            data["active"] = account_id
            self._save(data)
            return self._safe_status(data)

    def disconnect(self):
        """Sign out selected account; retain validated registration and stable host ID."""
        with self._locked():
            self._pending = None
            self._retry_client = None
            self._stop_listener()
            data = self._load()
            record = data["accounts"].get(data["active"])
            confirmed = not bool(record and record.get("refresh_token"))
            if record:
                try:
                    if record.get("refresh_token"):
                        endpoint = self._discovery().get("revocation_endpoint", "")
                        parsed = urlsplit(endpoint)
                        if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.query or parsed.fragment:
                            raise AuthError("Invalid revocation endpoint.", "discovery_error")
                        # One bounded retry on server failure. No automatic retry of rotating refresh.
                        for attempt in range(2):
                            try:
                                status, _ = self._request("POST", endpoint, {"token": record["refresh_token"],
                                    "token_type_hint": "refresh_token", "client_id": record["client_id"]})
                            except AuthError:
                                status = 503
                            if status == 200:
                                confirmed = True
                                break
                            if status < 500:
                                break
                            if attempt == 0:
                                time.sleep(0.25)
                except AuthError:
                    confirmed = False
                finally:
                    self._clear_tokens(record)
                    self._save(data)
            self._last_error = None if confirmed else "Signed out locally. Remote revocation was not confirmed; disconnect NAIA in ChatGPT Settings."
            result = self._safe_status(data)
            result["revocation_confirmed"] = confirmed
            return result

    def close(self):
        """Cancel pending authentication; existing credentials stay available on restart."""
        with self._mutex:
            self._pending = None
            self._stop_listener()
