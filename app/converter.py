"""Pure conversion helpers for x.ai/Grok authentication files.

The network-facing Device Flow lives in :mod:`app.oauth`. Keeping file format
conversion here makes the UI easy to test without making real auth requests.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
AUTH_KEY = f"{OIDC_ISSUER}::{CLIENT_ID}"
CLIPROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CLIPROXY_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/oauth2/token"
CLIPROXY_REDIRECT_URI = "http://127.0.0.1:56121/callback"
CLIPROXY_HEADERS = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


def b64url_decode(segment: str) -> bytes:
    """Decode a JWT segment, accepting missing base64 padding."""

    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Return a JWT payload or an empty mapping for opaque tokens."""

    try:
        pieces = token.split(".")
        if len(pieces) < 2:
            return {}
        payload = json.loads(b64url_decode(pieces[1]))
        return payload if isinstance(payload, dict) else {}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def rfc3339_ns(timestamp: float | None = None) -> str:
    dt = datetime.fromtimestamp(timestamp or time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".000000000Z"


def rfc3339_sec(timestamp: float | None = None) -> str:
    dt = datetime.fromtimestamp(timestamp or time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_sso_line(line: str) -> tuple[str, str]:
    """Parse a raw SSO line and optionally recover an email label.

    Accepted formats are a plain cookie, ``email----cookie`` and
    ``email----password----cookie``. Passwords are intentionally discarded.
    """

    value = line.strip()
    if not value or value.startswith("#"):
        return "", ""
    parts = [part.strip() for part in value.split("----") if part.strip()]
    if len(parts) >= 2:
        email = parts[0] if "@" in parts[0] else ""
        return parts[-1], email
    return value, ""


def load_sso_list(text: str, single: str | None = None) -> list[tuple[str, str]]:
    """Return ``[(cookie, email), ...]`` from textarea content."""

    if single and single.strip():
        return [(single.strip(), "")]
    result: list[tuple[str, str]] = []
    for line in text.splitlines():
        cookie, email = parse_sso_line(line)
        if cookie:
            result.append((cookie, email))
    return result


def token_to_auth_entry(token: dict[str, Any], email: str = "") -> tuple[str, dict[str, Any]]:
    access = str(token.get("access_token") or token.get("key") or "")
    refresh = str(token.get("refresh_token") or "")
    payload = decode_jwt_payload(access)
    user_id = str(payload.get("sub") or payload.get("principal_id") or "")
    principal_id = str(payload.get("principal_id") or user_id)
    principal_type = str(payload.get("principal_type") or "User")
    expires_in = int(token.get("expires_in") or 21600)
    expires_at = rfc3339_ns(float(payload["exp"])) if "exp" in payload else rfc3339_ns(time.time() + expires_in)
    create_time = rfc3339_ns(float(payload["iat"])) if payload.get("iat") else rfc3339_ns()
    entry = {
        "key": access,
        "auth_mode": "oidc",
        "create_time": create_time,
        "user_id": user_id,
        "email": email or str(token.get("_email") or token.get("email") or ""),
        "principal_type": principal_type,
        "principal_id": principal_id,
        "refresh_token": refresh,
        "expires_at": expires_at,
        "oidc_issuer": OIDC_ISSUER,
        "oidc_client_id": CLIENT_ID,
    }
    return AUTH_KEY, entry


def _safe_filename_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = cleaned.strip(" .")
    return cleaned[:160]


def cliproxy_filename(email: str = "", sub: str = "") -> str:
    label = _safe_filename_component(email) or _safe_filename_component(sub)
    if not label:
        label = f"anon_{secrets.token_hex(4)}"
    return f"xai-{label}.json"


def token_to_cliproxy_entry(token: dict[str, Any], email: str = "") -> tuple[str, dict[str, Any]]:
    access = str(token.get("access_token") or token.get("key") or "")
    refresh = str(token.get("refresh_token") or "")
    id_token = str(token.get("id_token") or "")
    token_type = str(token.get("token_type") or "Bearer")
    expires_in = int(token.get("expires_in") or 21600)
    access_payload = decode_jwt_payload(access)
    id_payload = decode_jwt_payload(id_token)
    sub = str(
        access_payload.get("sub")
        or access_payload.get("principal_id")
        or id_payload.get("sub")
        or ""
    )
    resolved_email = str(
        email
        or token.get("_email")
        or token.get("email")
        or id_payload.get("email")
        or access_payload.get("email")
        or ""
    )
    expired = rfc3339_sec(float(access_payload["exp"])) if "exp" in access_payload else rfc3339_sec(time.time() + expires_in)
    last_refresh = rfc3339_sec(float(access_payload["iat"])) if "iat" in access_payload else rfc3339_sec()
    entry = {
        "type": "xai",
        "auth_kind": "oauth",
        "access_token": access,
        "refresh_token": refresh,
        "token_type": token_type,
        "expires_in": expires_in,
        "expired": expired,
        "last_refresh": last_refresh,
        "email": resolved_email,
        "sub": sub,
        "base_url": CLIPROXY_BASE_URL,
        "token_endpoint": CLIPROXY_TOKEN_ENDPOINT,
        "redirect_uri": CLIPROXY_REDIRECT_URI,
        "disabled": False,
        "headers": dict(CLIPROXY_HEADERS),
        "id_token": id_token,
    }
    return cliproxy_filename(resolved_email, sub), entry


def auth_file_to_token(data: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Extract a token from flat xai or nested Grok auth JSON."""
    entries = auth_file_to_tokens(data)
    return entries[0] if entries else None


def auth_file_to_tokens(data: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Extract every account from flat or nested auth JSON."""

    if not isinstance(data, dict) or not data:
        return []

    if data.get("type") == "xai" or data.get("auth_kind") == "oauth":
        access = data.get("access_token") or data.get("key") or ""
        if access:
            return [
                (
                    {
                        "access_token": access,
                        "refresh_token": data.get("refresh_token") or "",
                        "token_type": data.get("token_type") or "Bearer",
                        "expires_in": int(data.get("expires_in") or 21600),
                        "id_token": data.get("id_token") or "",
                    },
                    str(data.get("email") or ""),
                )
            ]

    if "key" in data and ("refresh_token" in data or "auth_mode" in data):
        parsed = _entry_to_token(data)
        return [parsed] if parsed else []

    entries: list[tuple[dict[str, Any], str]] = []
    for value in data.values():
        if isinstance(value, dict) and (value.get("key") or value.get("access_token")):
            parsed = _entry_to_token(value)
            if parsed:
                entries.append(parsed)
    return entries


def _entry_to_token(entry: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    access = entry.get("access_token") or entry.get("key") or ""
    if not access:
        return None
    payload = decode_jwt_payload(str(access))
    exp_in = 21600
    if "exp" in payload and "iat" in payload:
        exp_in = max(1, int(payload["exp"]) - int(payload["iat"]))
    return {
        "access_token": access,
        "refresh_token": entry.get("refresh_token") or "",
        "token_type": entry.get("token_type") or "Bearer",
        "expires_in": int(entry.get("expires_in") or exp_in),
        "id_token": entry.get("id_token") or "",
    }, str(entry.get("email") or "")


def merge_auth_payload(existing: dict[str, Any], entry: dict[str, Any], unique: bool = True) -> dict[str, Any]:
    """Merge one Grok entry without overwriting another account."""

    output = dict(existing) if isinstance(existing, dict) else {}
    key = AUTH_KEY
    user_id = entry.get("user_id")
    if unique:
        key = f"{AUTH_KEY}::{user_id}" if user_id else f"{AUTH_KEY}::anon"
        if key in output:
            counter = 2
            candidate = f"{key}::{counter}"
            while candidate in output:
                counter += 1
                candidate = f"{key}::{counter}"
            key = candidate
    output[key] = entry
    return output


def convert_auth_document(data: dict[str, Any], email_override: str = "") -> tuple[str, dict[str, Any]]:
    documents = convert_auth_documents(data, email_override=email_override)
    if not documents:
        raise ValueError("无法识别 auth JSON 结构")
    return documents[0]


def convert_auth_documents(data: dict[str, Any], email_override: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Convert every account in an auth document to cliproxyapi entries."""

    documents: list[tuple[str, dict[str, Any]]] = []
    for token, email in auth_file_to_tokens(data):
        documents.append(token_to_cliproxy_entry(token, email=email_override or email))
    return documents


def serialize_json(data: dict[str, Any], compact: bool = False) -> bytes:
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
