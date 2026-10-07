"""YouTube OAuth(웹 애플리케이션 방식) 연결 및 업로드."""
import json
import os
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request as GRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import config
from .auth import require_admin

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = config.ROOT / "client_secret.json"
TOKEN = config.ROOT / "token.json"
REDIRECT_URI = f"{config.BASE_URL}/oauth/callback"

if config.BASE_URL.startswith("http://"):  # 로컬 개발(http) 허용
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

router = APIRouter()


def _flow() -> Flow:
    if not CLIENT_SECRET.exists():
        raise HTTPException(500, "client_secret.json 파일이 없습니다.")
    return Flow.from_client_secrets_file(str(CLIENT_SECRET), scopes=SCOPES, redirect_uri=REDIRECT_URI)


def load_credentials() -> Credentials | None:
    if not TOKEN.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            try:
                creds.refresh(GRequest())
                TOKEN.write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                return None
        else:
            return None
    return creds


@router.get("/api/youtube/status", dependencies=[Depends(require_admin)])
def status():
    return {"connected": load_credentials() is not None}


@router.get("/oauth/start", dependencies=[Depends(require_admin)])
def oauth_start(request: Request):
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = flow.code_verifier
    return RedirectResponse(url)


@router.get("/oauth/callback", dependencies=[Depends(require_admin)])
def oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<p>연결이 취소되었습니다: {error}</p><a href='/'>돌아가기</a>", status_code=400)
    if not state or state != request.session.get("oauth_state"):
        raise HTTPException(400, "잘못된 요청입니다. 다시 시도하세요.")
    flow = _flow()
    flow.code_verifier = request.session.get("oauth_verifier")
    flow.fetch_token(code=code)
    TOKEN.write_text(flow.credentials.to_json(), encoding="utf-8")
    return RedirectResponse("/", status_code=303)


def _api_error(e: HttpError) -> str:
    try:
        info = json.loads(e.content.decode())["error"]
        reason = (info.get("errors") or [{}])[0].get("reason", "")
        msg = info.get("message", "")
    except Exception:
        return f"유튜브 오류 ({e.resp.status})"
    hints = {
        "quotaExceeded": "오늘의 유튜브 API 업로드 한도를 초과했습니다. 내일 다시 시도하세요.",
        "uploadLimitExceeded": "채널의 일일 업로드 한도를 초과했습니다.",
        "youtubeSignupRequired": "해당 구글 계정에 유튜브 채널이 없습니다.",
        "forbidden": "권한이 없습니다. '유튜브 연결'을 다시 해주세요.",
    }
    return hints.get(reason, f"유튜브 오류: {msg or reason}")


def upload(path, title: str, privacy: str, on_progress: Callable[[float], None]) -> str:
    creds = load_credentials()
    if not creds:
        raise RuntimeError("유튜브가 연결되어 있지 않습니다. '유튜브 연결'을 먼저 해주세요.")
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {"title": title, "description": "", "categoryId": "22"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=16 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    retries = 0
    while resp is None:
        try:
            st, resp = req.next_chunk()
            if st:
                on_progress(st.progress())
            retries = 0
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                continue
            raise RuntimeError(_api_error(e))
        except (ConnectionError, TimeoutError, OSError):
            retries += 1
            if retries > 5:
                raise RuntimeError("네트워크 오류로 업로드가 중단되었습니다.")
    on_progress(1.0)
    return resp["id"]
