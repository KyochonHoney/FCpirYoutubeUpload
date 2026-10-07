"""병합 + 유튜브 업로드 작업 (백그라운드 스레드, 상태는 메모리).

흐름: 병합 → (미리보기 확인) → 유튜브 업로드. "바로 업로드"를 고르면 병합 직후 자동으로 업로드한다.
"""
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, merge, videos, youtube
from .auth import require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])

JOBS: dict[str, dict] = {}
_lock = threading.Lock()
PRIVATE_KEYS = ("file", "ids")  # 응답에 내보내지 않는 내부 값


class PublishReq(BaseModel):
    ids: list[str]
    title: str
    privacy: str = "private"
    encode: bool = False        # True: 항상 재인코딩 (안전 모드)
    upload: bool = True         # False: 병합만 하고 미리보기


class UploadReq(BaseModel):
    title: str
    privacy: str = "private"


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in PRIVATE_KEYS}


def _active() -> dict | None:
    return next((j for j in JOBS.values() if j["stage"] in ("merge", "upload")), None)


def _ready() -> dict | None:
    ready = [j for j in JOBS.values() if j["stage"] == "ready"]
    return ready[-1] if ready else None


def _validate(title: str, privacy: str):
    title = title.strip()
    if not title or len(title) > 100:
        raise HTTPException(400, "제목은 1~100자로 입력하세요.")
    if privacy not in ("private", "unlisted", "public"):
        raise HTTPException(400, "잘못된 공개 설정입니다.")
    return title


def _discard(job: dict):
    job["file"].unlink(missing_ok=True)
    JOBS.pop(job["id"], None)


def _do_upload(job: dict, title: str, privacy: str):
    try:
        job.update(stage="upload", progress=0.0, message="유튜브 업로드 중")
        vid = youtube.upload(job["file"], title, privacy, lambda p: job.update(progress=p))
        job.update(stage="done", progress=1.0, message="업로드 완료", url=f"https://www.youtube.com/watch?v={vid}")
        for vid_id in job["ids"]:  # 성공하면 원본 정리
            videos.delete_video(vid_id)
        job["file"].unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 - 사용자에게 메시지로 전달
        # 업로드 실패 시 병합본은 남겨 두어 다시 시도할 수 있게 한다
        job.update(stage="ready" if job["file"].exists() else "error", message="업로드 실패: " + (str(e) or "알 수 없는 오류"))


def _run(job: dict, metas: list[dict], title: str, privacy: str, encode: bool, upload: bool):
    try:
        job.update(stage="merge", progress=0.0, message="영상 병합 중")
        mode = merge.merge(metas, job["file"], lambda p: job.update(progress=p), force_encode=encode)
        how = "재인코딩 없이 이어붙임" if mode == "copy" else "재인코딩으로 병합"
    except Exception as e:  # noqa: BLE001
        job["file"].unlink(missing_ok=True)
        job.update(stage="error", message=str(e) or "병합 실패")
        return
    job.update(progress=1.0, merge_mode=mode)
    if upload:
        job["message"] = f"병합 완료 ({how})"
        _do_upload(job, title, privacy)
    else:
        job.update(stage="ready", message=f"병합 완료 ({how}) — 아래에서 재생해 확인하세요")


@router.post("/publish")
def publish(req: PublishReq):
    title = _validate(req.title, req.privacy)
    if not req.ids or len(set(req.ids)) != len(req.ids):
        raise HTTPException(400, "영상을 선택하세요.")
    if req.upload and youtube.load_credentials() is None:
        raise HTTPException(400, "유튜브가 연결되어 있지 않습니다. 먼저 '유튜브 연결'을 눌러주세요.")

    metas = []
    for vid in req.ids:
        m = videos.load_meta(videos.check_id(vid))
        if not m or not (config.UPLOAD_DIR / m["file"]).exists():
            raise HTTPException(400, "목록에 없는 영상이 있습니다. 새로고침 후 다시 시도하세요.")
        metas.append(m)

    with _lock:
        if _active():
            raise HTTPException(409, "이미 진행 중인 작업이 있습니다.")
        for old in [j for j in JOBS.values() if j["stage"] in ("ready", "error", "done")]:
            _discard(old)  # 이전 미리보기/결과 정리
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "stage": "merge", "progress": 0.0, "message": "준비 중", "url": None,
               "title": title, "merge_mode": None, "file": config.MERGED_DIR / f"{jid}.mp4", "ids": req.ids}
        JOBS[jid] = job
    threading.Thread(target=_run, args=(job, metas, title, req.privacy, req.encode, req.upload), daemon=True).start()
    return _public(job)


@router.post("/jobs/{job_id}/upload")
def upload_ready(job_id: str, req: UploadReq):
    """미리보기로 확인한 병합본을 그대로 유튜브에 업로드"""
    title = _validate(req.title, req.privacy)
    if youtube.load_credentials() is None:
        raise HTTPException(400, "유튜브가 연결되어 있지 않습니다. 먼저 '유튜브 연결'을 눌러주세요.")
    with _lock:
        job = JOBS.get(job_id)
        if not job or job["stage"] != "ready" or not job["file"].exists():
            raise HTTPException(409, "업로드할 병합본이 없습니다.")
        if _active():
            raise HTTPException(409, "이미 진행 중인 작업이 있습니다.")
        job.update(stage="upload", progress=0.0, title=title, message="유튜브 업로드 중")
    threading.Thread(target=_do_upload, args=(job, title, req.privacy), daemon=True).start()
    return _public(job)


@router.get("/jobs/{job_id}/video")
def job_video(job_id: str):
    """관리자 화면 미리보기용 (Range 요청 지원)"""
    job = JOBS.get(job_id)
    if not job or not job["file"].exists():
        raise HTTPException(404, "영상이 없습니다.")
    return FileResponse(job["file"], media_type="video/mp4")


@router.delete("/jobs/{job_id}")
def discard_job(job_id: str):
    job = JOBS.get(job_id)
    if job and job["stage"] in ("ready", "error", "done"):
        _discard(job)
    return {"ok": True}


@router.get("/jobs/active")
def active_job():
    job = _active() or _ready()
    return _public(job) if job else {"stage": "none"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return _public(job)
