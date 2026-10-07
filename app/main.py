from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, publish, videos, youtube

app = FastAPI(title="PIR 영상 업로더", docs_url=None, redoc_url=None)
app.include_router(videos.router)
app.include_router(publish.router)
app.include_router(youtube.router)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=config.BASE_URL.startswith("https"),
)
app.mount("/static", StaticFiles(directory=config.ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.ROOT / "app" / "templates")


def client_ip(request: Request) -> str:
    # nginx 뒤에서는 X-Forwarded-For 사용
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


@app.get("/")
def index(request: Request):
    if not auth.is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "index.html")


@app.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "legal.html", {"title": "개인정보처리방침", "kind": "privacy", "email": config.CONTACT_EMAIL})


@app.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse(request, "legal.html", {"title": "서비스 약관", "kind": "terms"})


@app.get("/login")
def login_page(request: Request):
    if auth.is_logged_in(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = client_ip(request)
    if auth.is_locked(ip):
        msg = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
    elif auth.check_credentials(ip, username, password):
        request.session["admin"] = True
        return RedirectResponse("/", status_code=303)
    else:
        msg = "아이디 또는 비밀번호가 올바르지 않습니다."
    return templates.TemplateResponse(request, "login.html", {"error": msg}, status_code=401)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
