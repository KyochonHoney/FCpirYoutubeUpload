"""영상 청크 업로드, 메타데이터(촬영 시작시간 등) 추출, 목록 관리."""
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from . import config
from .auth import require_admin

router = APIRouter(prefix="/api/videos", dependencies=[Depends(require_admin)])

KST = timezone(timedelta(hours=9))
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".3gp"}
TMP_DIR = config.UPLOAD_DIR / "_tmp"
TMP_DIR.mkdir(exist_ok=True)
MAX_CHUNKS = 5000  # 청크 8MB 기준 약 40GB


# ---------- 유틸 ----------
def make_id(key: str) -> str:
    """파일 식별 키(이름+크기+수정시간) → 고정 ID. 같은 파일 다시 올리면 이어받기/중복 판정"""
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def safe_ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"지원하지 않는 형식입니다: {ext or '(확장자 없음)'}")
    return ext


def meta_path(vid: str) -> Path:
    return config.UPLOAD_DIR / f"{vid}.json"


def check_id(vid: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{16}", vid):
        raise HTTPException(400, "잘못된 ID")
    return vid


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.year < 2000:  # 0 값(1904/1970) 메타데이터 무시
        return None
    return dt.astimezone(KST)


def probe(path: Path) -> dict:
    r = subprocess.run(
        [config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise HTTPException(400, "영상 파일을 읽을 수 없습니다 (손상되었거나 영상이 아닙니다).")
    return json.loads(r.stdout)


def build_meta(vid: str, path: Path, name: str, size: int, last_modified_ms: int | None) -> dict:
    info = probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise HTTPException(400, "영상 스트림이 없는 파일입니다.")

    fmt = info.get("format", {})
    created = parse_time((fmt.get("tags") or {}).get("creation_time")) or parse_time(
        (v.get("tags") or {}).get("creation_time")
    )
    source = "metadata"
    if not created and last_modified_ms:
        created = datetime.fromtimestamp(last_modified_ms / 1000, KST)
        source = "file"
    if not created:
        source = None

    try:
        n, d = v.get("r_frame_rate", "0/1").split("/")
        fps = round(int(n) / int(d), 2) if int(d) else 0
    except ValueError:
        fps = 0

    return {
        "id": vid,
        "name": name,
        "size": size,
        "file": path.name,
        "duration": float(fmt.get("duration") or v.get("duration") or 0),
        "start_time": created.isoformat() if created else None,
        "start_ts": created.timestamp() if created else None,
        "start_display": created.strftime("%Y-%m-%d %H:%M:%S") if created else None,
        "start_date": created.strftime("%Y%m%d") if created else None,
        "start_source": source,  # metadata = 촬영 메타데이터, file = 파일 수정시간(추정)
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": fps,
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name") if a else None,
    }


def load_meta(vid: str) -> dict | None:
    p = meta_path(vid)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def assemble(key: str, total: int, name: str, size: int, last_modified_ms: int | None) -> dict:
    vid = make_id(key)
    tmp = TMP_DIR / vid
    parts = [tmp / f"{i}.part" for i in range(total)]
    missing = [i for i, p in enumerate(parts) if not p.exists()]
    if missing:
        raise HTTPException(409, f"아직 올라오지 않은 조각이 있습니다: {missing[:5]}")

    ext = safe_ext(name)
    dest = config.UPLOAD_DIR / f"{vid}{ext}"
    with open(dest, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, 1024 * 1024)

    if dest.stat().st_size != size:
        dest.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, "파일 크기가 맞지 않습니다. 다시 올려주세요.")
    try:
        meta = build_meta(vid, dest, name, size, last_modified_ms)
    except HTTPException:
        dest.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    meta_path(vid).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return meta


# ---------- API ----------
@router.get("")
def list_videos():
    items = []
    for p in config.UPLOAD_DIR.glob("*.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if (config.UPLOAD_DIR / m["file"]).exists():
            items.append(m)
    # 시작시간 있는 것 먼저 시간순, 없는 것은 뒤로
    items.sort(key=lambda m: (m["start_ts"] is None, m["start_ts"] or 0, m["name"]))
    return items


@router.get("/status")
def upload_status(key: str, name: str):
    """이어받기용: 이미 완성된 파일이면 바로 반환, 아니면 받은 조각 번호 목록"""
    safe_ext(name)
    vid = make_id(key)
    done = load_meta(vid)
    if done and (config.UPLOAD_DIR / done["file"]).exists():
        return {"complete": True, "video": done, "received": []}
    tmp = TMP_DIR / vid
    received = sorted(int(p.stem) for p in tmp.glob("*.part")) if tmp.exists() else []
    return {"complete": False, "video": None, "received": received}


@router.post("/chunk")
def upload_chunk(
    key: str = Form(...),
    index: int = Form(...),
    total: int = Form(...),
    name: str = Form(...),
    size: int = Form(...),
    last_modified: int | None = Form(None),
    chunk: UploadFile = File(...),
):
    safe_ext(name)
    if not (0 <= index < total <= MAX_CHUNKS):
        raise HTTPException(400, "잘못된 조각 번호")
    vid = make_id(key)
    tmp = TMP_DIR / vid
    tmp.mkdir(parents=True, exist_ok=True)

    part = tmp / f"{index}.part"
    with open(tmp / f"{index}.tmp", "wb") as f:
        shutil.copyfileobj(chunk.file, f, 1024 * 1024)
    (tmp / f"{index}.tmp").replace(part)  # 원자적으로 확정 (끊긴 조각 방지)

    if all((tmp / f"{i}.part").exists() for i in range(total)):
        return {"done": True, "video": assemble(key, total, name, size, last_modified)}
    return {"done": False, "video": None}


@router.post("/finalize")
def finalize(
    key: str = Form(...),
    total: int = Form(...),
    name: str = Form(...),
    size: int = Form(...),
    last_modified: int | None = Form(None),
):
    """모든 조각이 이미 있는데 조립이 안 된 경우(중간에 끊김) 조립만 다시 실행"""
    return {"done": True, "video": assemble(key, total, name, size, last_modified)}


@router.delete("/{vid}")
def delete_video(vid: str):
    check_id(vid)
    m = load_meta(vid)
    if m:
        (config.UPLOAD_DIR / m["file"]).unlink(missing_ok=True)
    meta_path(vid).unlink(missing_ok=True)
    shutil.rmtree(TMP_DIR / vid, ignore_errors=True)
    return {"ok": True}
