"""x.ai OAuth Device Flow implementation used by the conversion jobs."""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .converter import CLIENT_ID, OIDC_ISSUER, decode_jwt_payload

SCOPES = (
    "openid profile email offline_access grok-cli:access "
    "api:access conversations:read conversations:write"
)


class RateLimitedError(RuntimeError):
    """The account hit an x.ai rate limit after the configured retries."""


class MissingOAuthDependencyError(RuntimeError):
    """The optional browser-impersonating HTTP dependency is not installed."""


def is_rate_limited(url: str, body: str = "") -> bool:
    blob = f"{url}\n{body}".lower()
    return any(value in blob for value in ("rate_limited", "rate-limited", "too_many_requests", "ratelimit", "429"))


def backoff_sec(base: float, attempt: int, cap: float = 120.0) -> float:
    base = base if base > 0 else 10.0
    attempt = max(1, attempt)
    delay = min(base * (2 ** min(attempt - 1, 4)), cap)
    return delay + secrets.randbelow(5)


@dataclass
class Pacer:
    """Adaptive delay shared by a batch job."""

    base: float = 20.0
    max_delay: float = 180.0
    hits: int = 0

    def __post_init__(self) -> None:
        self.base = max(0.0, float(self.base)) or 20.0
        self.max_delay = max(30.0, float(self.max_delay))
        self.current = self.base

    def on_rate_limit(self) -> None:
        self.hits += 1
        self.current = min(max(self.current * 1.6, self.current + 20, 30.0), self.max_delay)

    def on_success(self) -> None:
        if self.hits > 0:
            self.hits -= 1
        if self.current > self.base:
            self.current = max(self.base, self.current * 0.88)

    def wait_between(self, remaining: bool, sleep: Callable[[float], None] = time.sleep) -> float:
        if not remaining or self.current <= 0:
            return 0.0
        total = self.current + secrets.randbelow(6)
        sleep(total)
        return total


def _json_response(resp: Any) -> dict[str, Any]:
    data = json.loads(resp.read())
    return data if isinstance(data, dict) else {}


def request_device_code() -> dict[str, Any] | None:
    data = urllib.parse.urlencode({"client_id": CLIENT_ID, "scope": SCOPES}).encode()
    req = urllib.request.Request(
        f"{OIDC_ISSUER}/oauth2/device/code",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _json_response(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def poll_token(device_code: str, interval: int, expires_in: int, timeout: int = 1800) -> dict[str, Any] | None:
    deadline = time.time() + min(int(expires_in), timeout)
    while time.time() < deadline:
        time.sleep(max(0, interval))
        data = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_code,
            }
        ).encode()
        req = urllib.request.Request(
            f"{OIDC_ISSUER}/oauth2/token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = _json_response(resp)
                return result or None
        except urllib.error.HTTPError as exc:
            try:
                error_body = json.loads(exc.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                error_body = {}
            error = error_body.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            return None
        except (urllib.error.URLError, json.JSONDecodeError):
            return None
    return None


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    if not access_token:
        return {}
    req = urllib.request.Request(
        f"{OIDC_ISSUER}/oauth2/userinfo",
        method="GET",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json_response(resp)
            return result if isinstance(result, dict) else {}
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return {}


def enrich_token_with_userinfo(token: dict[str, Any]) -> dict[str, Any]:
    if not token or token.get("_email") or token.get("email"):
        return token
    info = fetch_userinfo(str(token.get("access_token") or token.get("key") or ""))
    if info.get("email"):
        token["_email"] = info["email"]
        token["_email_verified"] = bool(info.get("email_verified"))
        token["_name"] = info.get("name") or ""
    return token


def _load_curl_session() -> Any:
    try:
        from curl_cffi import requests
    except ImportError as exc:
        raise MissingOAuthDependencyError(
            "SSO 验证需要 curl_cffi。请先运行 `python -m pip install -r requirements.txt`。"
        ) from exc
    return requests.Session()


def sso_to_token(
    sso_cookie: str,
    max_retries: int = 8,
    base_delay: float = 15.0,
    log: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Exchange one SSO cookie for an OAuth token through Device Flow."""

    write_log = log or (lambda _message: None)
    session = _load_curl_session()
    session.cookies.set("sso", sso_cookie, domain=".x.ai")
    try:
        response = session.get("https://accounts.x.ai/", impersonate="chrome", timeout=15)
    except Exception as exc:  # curl_cffi exposes request-specific exception classes
        write_log(f"网络错误：{exc}")
        return None
    if "sign-in" in response.url or "sign-up" in response.url:
        write_log("SSO Cookie 无效或已过期")
        return None

    device: dict[str, Any] | None = None

    def fresh_device() -> bool:
        nonlocal device
        device = request_device_code()
        if not device:
            write_log("无法申请 Device Code")
            return False
        verification_uri = device.get("verification_uri_complete")
        if not verification_uri:
            write_log("Device Code 响应缺少验证地址")
            return False
        try:
            session.get(verification_uri, impersonate="chrome", timeout=15)
        except Exception as exc:
            write_log(f"打开授权页失败：{exc}")
            return False
        return True

    if not fresh_device() or device is None:
        return None

    rate_hits = 0
    verified = False
    approved = False
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            response = session.post(
                f"{OIDC_ISSUER}/oauth2/device/verify",
                data={"user_code": device["user_code"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=15,
                allow_redirects=True,
            )
            if is_rate_limited(response.url, (response.text or "")[:400]):
                rate_hits += 1
                delay = backoff_sec(base_delay, attempt, 180)
                write_log(f"verify 遇到限流，{delay:.0f}s 后重试 ({attempt}/{max_retries})")
                sleep(delay)
                if not fresh_device():
                    return None
                continue
            if "consent" not in response.url:
                write_log("verify 未进入授权确认页")
                return None
            verified = True
        except Exception as exc:
            delay = backoff_sec(base_delay, attempt, 120)
            write_log(f"verify 异常，{delay:.0f}s 后重试：{exc}")
            sleep(delay)
            continue

        try:
            response = session.post(
                f"{OIDC_ISSUER}/oauth2/device/approve",
                data={
                    "user_code": device["user_code"],
                    "action": "allow",
                    "principal_type": "User",
                    "principal_id": "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=15,
                allow_redirects=True,
            )
            if is_rate_limited(response.url, (response.text or "")[:400]):
                rate_hits += 1
                delay = backoff_sec(base_delay, attempt, 180)
                write_log(f"approve 遇到限流，{delay:.0f}s 后重试 ({attempt}/{max_retries})")
                sleep(delay)
                if not fresh_device():
                    return None
                verified = False
                continue
            if "done" not in response.url:
                write_log("approve 未返回完成状态")
                return None
            approved = True
            break
        except Exception as exc:
            delay = backoff_sec(base_delay, attempt, 120)
            write_log(f"approve 异常，{delay:.0f}s 后重试：{exc}")
            sleep(delay)

    if not verified or not approved:
        if rate_hits:
            raise RateLimitedError("verify/approve 重试次数已用尽")
        return None

    result = poll_token(
        str(device["device_code"]),
        int(device.get("interval", 5)),
        int(device.get("expires_in", 1800)),
        timeout=int(device.get("expires_in", 1800)),
    )
    return enrich_token_with_userinfo(result) if result else None


def token_identity(token: dict[str, Any]) -> tuple[str, str]:
    """Return a safe, non-secret identity tuple for job progress display."""

    payload = decode_jwt_payload(str(token.get("access_token") or token.get("key") or ""))
    return str(token.get("_email") or token.get("email") or ""), str(payload.get("sub") or "")
