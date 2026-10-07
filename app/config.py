import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ADMIN_ID = os.getenv("ADMIN_ID", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")  # 개인정보처리방침에 표시할 운영자 문의 이메일

FFMPEG = os.getenv("FFMPEG_PATH") or "ffmpeg"
FFPROBE = os.getenv("FFPROBE_PATH") or "ffprobe"

STORAGE = ROOT / "storage"
UPLOAD_DIR = STORAGE / "uploads"
MERGED_DIR = STORAGE / "merged"
for d in (UPLOAD_DIR, MERGED_DIR):
    d.mkdir(parents=True, exist_ok=True)

if not (ADMIN_ID and ADMIN_PASSWORD and SECRET_KEY):
    raise RuntimeError(".env에 ADMIN_ID, ADMIN_PASSWORD, SECRET_KEY를 설정하세요.")
