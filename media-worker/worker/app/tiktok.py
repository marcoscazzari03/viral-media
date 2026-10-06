"""TikTok Content Posting API: login (OAuth v2), creator info, direct post / inbox upload, status.

Only our own TikTok account is connected. Videos are pulled by TikTok from our verified domain
(PULL_FROM_URL), so they must be served under PUBLIC_BASE_URL (https://DOMAIN/files/...).
The access token (24h) is refreshed automatically with the refresh token (365 days).
"""

import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", f"{PUBLIC_BASE_URL}/tiktok/callback")
SCOPES = os.environ.get("TIKTOK_SCOPES", "user.info.basic,video.upload,video.publish")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/media"))
TOKEN_FILE = DATA_DIR / "tiktok" / "token.json"  # outside /files: never served publicly

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
_lock = threading.Lock()


class TikTokError(Exception):
    pass


def configured() -> bool:
    return bool(CLIENT_KEY and CLIENT_SECRET)


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_key": CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    })


def _save(tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tok))
    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_FILE)


def _load() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    return json.loads(TOKEN_FILE.read_text())


def _token_request(data: dict) -> dict:
    r = requests.post(f"{API}/oauth/token/", data={"client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET, **data},
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    body = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in body:
        raise TikTokError(body.get("error_description") or body.get("error") or f"token request failed ({r.status_code})")
    now = time.time()
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "open_id": body.get("open_id"),
        "scope": body.get("scope"),
        "expires_at": now + int(body.get("expires_in", 86400)) - 300,
        "refresh_expires_at": now + int(body.get("refresh_expires_in", 31536000)),
    }


def exchange_code(code: str) -> dict:
    tok = _token_request({"code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI})
    _save(tok)
    return tok


def access_token() -> str:
    with _lock:
        tok = _load()
        if not tok:
            raise TikTokError("TikTok account not connected: open /panel and connect it")
        if time.time() >= tok["expires_at"]:
            if time.time() >= tok.get("refresh_expires_at", 0):
                raise TikTokError("TikTok login expired: open /panel and connect the account again")
            refreshed = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
            tok.update(refreshed)
            _save(tok)
        return tok["access_token"]


def status() -> dict:
    tok = _load()
    if not tok:
        return {"configured": configured(), "connected": False}
    return {"configured": configured(), "connected": True, "scope": tok.get("scope"),
            "refresh_expires_at": int(tok.get("refresh_expires_at", 0))}


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _api(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    r = requests.request(method, f"{API}{path}", params=params, json=payload, timeout=30,
                         headers={"Authorization": f"Bearer {access_token()}",
                                  "Content-Type": "application/json; charset=UTF-8"})
    body = r.json() if r.content else {}
    err = body.get("error") or {}
    if r.status_code != 200 or (err.get("code") not in (None, "", "ok")):
        raise TikTokError(f"{err.get('code', r.status_code)}: {err.get('message', 'request failed')}")
    return body.get("data") or {}


def user_info() -> dict:
    return _api("GET", "/user/info/", params={"fields": "open_id,avatar_url,display_name"}).get("user", {})


def creator_info() -> dict:
    """Must be called before every direct post: privacy options and interaction settings can change."""
    return _api("POST", "/post/publish/creator_info/query/", payload={})


def check_video_url(video_url: str) -> None:
    if not PUBLIC_BASE_URL or not video_url.startswith(PUBLIC_BASE_URL + "/files/"):
        raise TikTokError("video_url must be one of our rendered files (verified domain)")


def direct_post(video_url: str, title: str, privacy_level: str, disable_comment: bool = False,
                disable_duet: bool = False, disable_stitch: bool = False, brand_content: bool = False,
                brand_organic: bool = False, cover_ms: int = 1000) -> str:
    check_video_url(video_url)
    info = creator_info()
    options = info.get("privacy_level_options") or []
    if privacy_level not in options:
        raise TikTokError(f"privacy_level must be one of {options}")
    if brand_content and privacy_level == "SELF_ONLY":
        raise TikTokError("branded content cannot be private (SELF_ONLY)")
    data = _api("POST", "/post/publish/video/init/", payload={
        "post_info": {
            "title": title[:2200],
            "privacy_level": privacy_level,
            # settings the creator turned off in TikTok can't be enabled by us
            "disable_comment": bool(disable_comment or info.get("comment_disabled")),
            "disable_duet": bool(disable_duet or info.get("duet_disabled")),
            "disable_stitch": bool(disable_stitch or info.get("stitch_disabled")),
            "video_cover_timestamp_ms": int(cover_ms),
            "brand_content_toggle": bool(brand_content),
            "brand_organic_toggle": bool(brand_organic),
        },
        "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
    })
    return data["publish_id"]


def inbox_upload(video_url: str) -> str:
    """Sends the video to the creator's TikTok inbox as a draft: they finish and post it in the app."""
    check_video_url(video_url)
    data = _api("POST", "/post/publish/inbox/video/init/",
                payload={"source_info": {"source": "PULL_FROM_URL", "video_url": video_url}})
    return data["publish_id"]


def publish_status(publish_id: str) -> dict:
    """status: PROCESSING_DOWNLOAD | PROCESSING_UPLOAD | SEND_TO_USER_INBOX | PUBLISH_COMPLETE | FAILED"""
    return _api("POST", "/post/publish/status/fetch/", payload={"publish_id": publish_id})
