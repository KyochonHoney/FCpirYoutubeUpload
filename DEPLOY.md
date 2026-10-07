# 서버 배포 가이드 (Ubuntu + Apache, 도메인 upload.example.com)

앱 경로는 **`/var/www/html/upload`** 입니다. 웹 루트 안이므로 `.env`, `client_secret.json`이 웹으로 노출되지 않게 5번의 차단 설정(`FilesMatch`, `ProxyPass`)을 반드시 적용하세요.

## 0. 사전 준비
- Ubuntu 서버 (sudo 권한), 디스크 여유 (영상 총합의 2배 이상 + 여유)
- DNS: `upload` A 레코드 → 서버 공인 IP
- 구글 콘솔 리디렉션 URI에 `https://upload.example.com/oauth/callback` 등록 (완료)

## 1. 패키지 설치
```bash
sudo apt update
sudo apt -y install python3 python3-venv python3-pip ffmpeg certbot python3-certbot-apache
ffmpeg -version | head -1          # 설치 확인
python3 --version                  # 3.10 이상이어야 함

# 프록시 모듈 활성화
sudo a2enmod proxy proxy_http headers ssl rewrite
sudo systemctl restart apache2
```
방화벽을 쓰고 있다면: `sudo ufw allow 'Apache Full'`

## 2. 코드 올리기 (내 PC → 서버)
PowerShell에서 (user@서버IP 는 본인 값). `.venv`, `.env`, `storage`, `token.json`은 올리지 않습니다.
```powershell
cd C:\xampp\htdocs\youtube
scp -r app requirements.txt client_secret.json user@서버IP:/tmp/pir/
```
서버에서:
```bash
sudo adduser --system --group --home /var/www/html/upload pir
sudo mkdir -p /var/www/html/upload && sudo cp -r /tmp/pir/* /var/www/html/upload/
sudo chown -R pir:pir /var/www/html/upload
sudo -u pir python3 -m venv /var/www/html/upload/.venv
sudo -u pir /var/www/html/upload/.venv/bin/pip install -r /var/www/html/upload/requirements.txt
```

## 3. 서버용 .env 만들기
```bash
sudo -u pir nano /var/www/html/upload/.env
```
```
ADMIN_ID=원하는-아이디
ADMIN_PASSWORD=서버용-긴-비밀번호
SECRET_KEY=아래 명령으로 생성한 값
BASE_URL=https://upload.example.com
CONTACT_EMAIL=개인정보처리방침에 표시할-문의-이메일
FFMPEG_PATH=
FFPROBE_PATH=
```
SECRET_KEY 생성: `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`
(`FFMPEG_PATH`는 비워두면 PATH의 ffmpeg를 씁니다.)
```bash
sudo chmod 600 /var/www/html/upload/.env /var/www/html/upload/client_secret.json
```

## 4. systemd 서비스 (앱을 계속 실행)
`/etc/systemd/system/pir.service`
```ini
[Unit]
Description=PIR uploader
After=network.target

[Service]
User=pir
Group=pir
WorkingDirectory=/var/www/html/upload
ExecStart=/var/www/html/upload/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pir
sudo systemctl status pir                       # active (running) 확인
curl -I http://127.0.0.1:8000/login             # 서버 안에서 200 확인
journalctl -u pir -f                            # 로그 보기
```
**워커는 반드시 1개**입니다 (작업 진행 상태를 메모리에 보관).

## 5. Apache 가상호스트
`/etc/apache2/sites-available/upload.conf`
```apache
<VirtualHost *:80>
    ServerName upload.example.com

    # 이미 DocumentRoot /var/www/html/upload 가 있어도, 아래 ProxyPass 가 모든 요청을 앱으로 보내므로 파일은 직접 서빙되지 않음
    # 만약을 위한 이중 차단: 비밀 파일/저장소는 어떤 경우에도 접근 불가
    <FilesMatch "^(\.env.*|client_secret.*\.json|token\.json|requirements\.txt)$">
        Require all denied
    </FilesMatch>
    <DirectoryMatch "^/var/www/html/upload/(storage|app|\.venv)">
        Require all denied
    </DirectoryMatch>
    ProxyPreserveHost On
    ProxyAddHeaders Off
    RequestHeader set X-Forwarded-Proto "https"
    RequestHeader set X-Forwarded-For expr=%{REMOTE_ADDR}   # 클라이언트가 IP를 위조하지 못하게 덮어씀

    ProxyTimeout 600
    Timeout 600
    LimitRequestBody 0

    ProxyPass        / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    ErrorLog  ${APACHE_LOG_DIR}/upload-error.log
    CustomLog ${APACHE_LOG_DIR}/upload-access.log combined
</VirtualHost>
```
```bash
sudo a2ensite upload
sudo apache2ctl configtest          # Syntax OK 확인
sudo systemctl reload apache2
```
다른 사이트가 있어도 `ServerName`이 달라서 영향이 없습니다.

## 6. HTTPS (Let's Encrypt)
DNS가 서버 IP를 가리키는 것을 확인한 뒤 실행합니다 (`ping upload.example.com`).
```bash
sudo certbot --apache -d upload.example.com
```
이메일 입력, 약관 동의, HTTP→HTTPS 리다이렉트 선택. 갱신은 자동입니다 (`sudo certbot renew --dry-run`으로 확인).

## 7. 동작 확인과 유튜브 연결
1. `https://upload.example.com` → 로그인 화면
2. 로그인 → 맨 위 **"유튜브 연결"** → 구글 허용 → "✔ 유튜브 연결됨"
3. 짧은 영상 2개로 비공개 시험 업로드
4. `https://upload.example.com/privacy`, `/terms`가 로그인 없이 열리는지 확인

## 8. 업데이트 방법
```powershell
scp -r app user@서버IP:/tmp/pir/
```
```bash
sudo cp -r /tmp/pir/app /var/www/html/upload/ && sudo chown -R pir:pir /var/www/html/upload/app && sudo systemctl restart pir
```

## 9. 점검/운영 메모
- 디스크: `df -h`. 임시 파일은 `/var/www/html/upload/storage` (업로드 성공 시 자동 삭제)
- 영상 규격이 서로 다르면 재인코딩으로 CPU를 오래 씁니다. 같은 규격이면 `-c copy`로 빠르게 끝납니다.
- `token.json`은 서버에서 연결할 때 `/var/www/html/upload/token.json`으로 생성 (분실 시 재연결)
- `.env` 수정 후에는 `sudo systemctl restart pir`
- 문제 시: `journalctl -u pir -n 100`, `sudo tail /var/log/apache2/upload-error.log`
- 외부에서 `https://upload.example.com/.env` 가 열리지 않는지 확인 (404/401이어야 정상)


