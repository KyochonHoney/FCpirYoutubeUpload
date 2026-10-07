"""ffmpeg로 여러 영상을 순서대로 하나로 병합."""
import json
import subprocess
from pathlib import Path
from typing import Callable

from . import config


def _probe(path: Path) -> dict:
    r = subprocess.run(
        [config.FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"영상을 읽을 수 없습니다: {path.name}")
    streams = json.loads(r.stdout).get("streams", [])
    v = next(s for s in streams if s.get("codec_type") == "video")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(sd["rotation"]) % 360
    if not rot and (v.get("tags") or {}).get("rotate"):
        rot = int(v["tags"]["rotate"]) % 360

    w, h = v["width"], v["height"]
    dw, dh = (h, w) if rot in (90, 270) else (w, h)  # 화면에 보이는 크기
    return {
        "vcodec": v.get("codec_name"), "w": w, "h": h, "dw": dw, "dh": dh,
        "fps": v.get("r_frame_rate"), "pix_fmt": v.get("pix_fmt"), "rot": rot,
        "acodec": a.get("codec_name") if a else None,
        "sr": a.get("sample_rate") if a else None,
        "ch": a.get("channels") if a else None,
    }


def can_copy(infos: list[dict]) -> bool:
    """모든 영상의 규격이 같고 mp4에 그대로 담을 수 있으면 재인코딩 없이 이어붙임"""
    first = infos[0]
    if first["vcodec"] not in ("h264", "hevc") or first["acodec"] not in ("aac", "mp3", None):
        return False
    keys = ("vcodec", "w", "h", "fps", "pix_fmt", "rot", "acodec", "sr", "ch")
    return all(all(i[k] == first[k] for k in keys) for i in infos)


def _run(cmd: list[str], total: float, on_progress: Callable[[float], None]):
    log = config.MERGED_DIR / "ffmpeg.log"
    with open(log, "w", encoding="utf-8", errors="replace") as err:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    sec = int(line.split("=")[1]) / 1_000_000
                    on_progress(min(sec / total, 1.0) if total else 0)
                except ValueError:
                    pass
        p.wait()
    if p.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace")[-600:]
        raise RuntimeError("병합 실패: " + tail.strip().splitlines()[-1] if tail.strip() else "병합 실패")


def merge(videos: list[dict], out_path: Path, on_progress: Callable[[float], None], force_encode: bool = False) -> str:
    """videos: 순서대로 정렬된 메타 목록. 반환값: 사용한 방식('copy' | 'encode')
    force_encode=True 면 규격이 같아도 재인코딩 (가변 프레임레이트 영상의 끊김 방지)"""
    paths = [config.UPLOAD_DIR / v["file"] for v in videos]
    infos = [_probe(p) for p in paths]
    total = sum(v["duration"] for v in videos)
    base = [config.FFMPEG, "-y", "-v", "error", "-progress", "pipe:1", "-nostats"]
    # 복사 병합 시 타임스탬프를 새로 만들어 이음매 끊김을 줄임
    ts_fix_in = ["-fflags", "+genpts"]
    ts_fix_out = ["-avoid_negative_ts", "make_zero"]

    if len(paths) == 1 and not force_encode:
        _run(base + ts_fix_in + ["-i", str(paths[0]), "-c", "copy"] + ts_fix_out + ["-movflags", "+faststart", str(out_path)],
             total, on_progress)
        return "copy"

    if not force_encode and len(paths) > 1 and can_copy(infos):
        lst = config.MERGED_DIR / "concat.txt"
        lst.write_text(
            "".join("file '{}'\n".format(str(p).replace("\\", "/").replace("'", "'\\''")) for p in paths),
            encoding="utf-8",
        )
        try:
            _run(base + ts_fix_in + ["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy"] + ts_fix_out
                 + ["-movflags", "+faststart", str(out_path)], total, on_progress)
        finally:
            lst.unlink(missing_ok=True)
        return "copy"

    # 규격이 다르면 첫 영상 기준으로 맞춰서 재인코딩
    W, H = infos[0]["dw"] // 2 * 2, infos[0]["dh"] // 2 * 2
    fps = 30
    try:
        n, d = infos[0]["fps"].split("/")
        fps = round(int(n) / int(d)) or 30
    except (ValueError, ZeroDivisionError, AttributeError):
        pass

    cmd = list(base)
    parts = []
    for i, (p, v, info) in enumerate(zip(paths, videos, infos)):
        cmd += ["-i", str(p)]
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p[v{i}]"
        )
        if info["acodec"]:
            parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:  # 소리 없는 영상은 무음으로 채움
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={v['duration']:.3f}[a{i}]")
    n = len(paths)
    parts.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    # fps 필터로 일정한 프레임레이트(CFR)를 만들고, 오디오는 aresample=async로 싱크 유지
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out_path)]
    _run(cmd, total, on_progress)
    return "encode"
