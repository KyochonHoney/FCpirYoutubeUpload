# PIR 영상 병합 & 유튜브 업로더 — 기획서

## 1. 목표
관리자가 로그인한 뒤 여러 동영상을 올리고, 촬영 시작시간 순서대로(또는 직접 정한 순서로) 하나의 파일로 병합해 유튜브에 한 번에 업로드한다.
업로드 제목은 `YYYYMMDD PIR풋` 형식이다. (예: `20261007 PIR풋`)

## 2. 확정 사항
| 항목 | 결정 |
|---|---|
| 백엔드 | Python + FastAPI |
| DB | 사용 안 함 (하드코딩 / 파일 기반) |
| 관리자 계정 | 1개, `.env`에 저장 |
| 순서 지정 | 드래그 앤 드롭 + 번호 직접 입력 (둘 다 쉬움) |
| 촬영 시작시간 | 영상 메타데이터에서 읽어 목록에 표시, 기본 정렬 기준 |
| 제목 | `YYYYMMDD PIR풋` 자동 생성 |

## 3. 사용자 흐름
1. `/login` 에서 관리자 ID/비밀번호 입력
2. 메인 화면에 영상 파일을 드래그해서 올림 (여러 개 한 번에 가능)
3. 각 영상이 카드로 표시됨: 파일명, 길이, **촬영 시작시간**, 크기
4. 기본 순서는 촬영 시작시간 오름차순으로 자동 정렬
5. 순서 변경: 카드를 드래그하거나 번호 칸에 숫자를 직접 입력, 또는 "시작시간순 정렬" 버튼 클릭
6. 제목 확인 (자동 `YYYYMMDD PIR풋`, 필요 시 수정 가능), 공개 설정 선택
7. "병합 후 업로드" 클릭 → 진행 상태(병합 → 업로드 → 완료) 표시
8. 완료되면 유튜브 링크 표시

## 4. 기술 설계

### 4.1 구조
```
youtube/
├─ app/
│  ├─ main.py          # FastAPI 앱, 라우트
│  ├─ auth.py          # 로그인/세션 (.env 계정 대조)
│  ├─ videos.py        # 업로드 저장, ffprobe 메타 추출
│  ├─ merge.py         # ffmpeg 병합
│  ├─ youtube.py       # YouTube Data API 업로드
│  ├─ jobs.py          # 백그라운드 작업 상태 (메모리 딕셔너리)
│  └─ templates/ static/   # 로그인/메인 화면 (Jinja2 + 바닐라 JS, SortableJS)
├─ storage/            # uploads/, merged/ (임시 파일)
├─ .env                # 계정, 시크릿
├─ client_secret.json  # 구글 OAuth 클라이언트
├─ token.json          # 최초 인증 후 자동 생성
└─ requirements.txt
```

### 4.2 .env
```
ADMIN_ID=admin
ADMIN_PASSWORD=change-me
SECRET_KEY=랜덤문자열        # 세션 쿠키 서명
```

### 4.3 인증
- `POST /login` → .env 값과 비교(`secrets.compare_digest`), 성공 시 서명된 세션 쿠키 발급 (Starlette `SessionMiddleware`)
- 로그인 필요한 모든 라우트는 의존성(`Depends`)으로 보호
- 로그인 실패 반복 방지를 위한 간단한 시도 횟수 제한

### 4.4 촬영 시작시간 추출
우선순위:
1. `ffprobe`의 `format.tags.creation_time` (카메라/휴대폰 촬영 메타데이터)
2. 없으면 파일의 `lastModified` (브라우저가 업로드 시 함께 전달)
3. 둘 다 불확실하면 "시간 정보 없음" 표시 후 수동 순서에 맡김

표시 형식은 `2026-10-07 14:32:05`이고, 타임존은 한국시간(KST)으로 변환한다.

### 4.5 병합 (ffmpeg)
- 모든 영상이 같은 코덱/해상도/프레임레이트면 `concat demuxer` + `-c copy` (재인코딩 없음, 빠름)
- 하나라도 다르면 자동으로 재인코딩(H.264/AAC, 기준 해상도에 맞춤)으로 폴백
- 사전 검사: ffprobe로 코덱·해상도·fps를 비교해 방식을 결정

### 4.6 유튜브 업로드
- YouTube Data API v3 `videos.insert`, resumable upload(대용량 대응)
- OAuth는 최초 1회 브라우저 인증 → `token.json` 저장 후 자동 갱신
- 제목: `{YYYYMMDD} PIR풋` — 날짜는 **가장 이른 촬영 시작시간의 날짜**(없으면 오늘)
- 기본 공개 설정: **비공개(private)**, 화면에서 변경 가능

### 4.7 API
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET/POST | `/login`, `/logout` | 로그인/로그아웃 |
| GET | `/` | 메인 화면 |
| POST | `/api/videos/chunk` | 청크 업로드 (이어받기 지원), 마지막 청크에서 조립 + 메타 추출 |
| DELETE | `/api/videos/{id}` | 영상 제거 |
| POST | `/api/publish` | `{순서 id 목록, 제목, 공개설정}` → 병합+업로드 작업 시작 |
| GET | `/api/jobs/{id}` | 진행 상태 조회 (폴링) |

## 5. UI 원칙 (쉬움이 최우선)
- 화면 하나에서 모두 처리: 드롭존 → 순서 목록 → 업로드 버튼
- 카드 전체가 드래그 핸들, 좌측에 큰 순번 입력칸 (숫자 바꾸면 즉시 재정렬)
- 촬영 시작시간을 카드에 크게 표시, 시간순과 어긋난 경우 시각적으로 표시
- 진행률 표시 바, 오류는 한글로 알려줌

## 6. 사전 준비물 (사용자 측)
1. **ffmpeg / ffprobe** 설치 및 PATH 등록
2. **Google Cloud 프로젝트**에서 YouTube Data API v3 활성화 + OAuth 클라이언트(데스크톱) 생성 → `client_secret.json`
3. 참고: API 심사 전(미인증 프로젝트)은 업로드 영상이 **비공개로 강제**될 수 있음 → 심사 신청 또는 본인 계정 테스트 사용자 등록 필요. 또한 API 일일 할당량상 업로드는 하루 약 6건 수준

## 7. 개발 단계
1. 프로젝트 뼈대 + `.env` 로그인
2. 영상 업로드 + ffprobe 메타 추출 + 목록 UI(드래그/번호 정렬)
3. ffmpeg 병합
4. YouTube OAuth + 업로드 + 진행 상태
5. 예외 처리, 임시 파일 정리, 마무리 테스트

## 8. 배포 및 접속 환경 (서버 업로드 방식)
- **배포 대상**: Ubuntu VPS (대역폭 포함 요금제 권장: Hetzner, Oracle Cloud 무료 티어, Lightsail 등). 개발은 로컬 Windows, 배포는 같은 코드를 서버에 올림
- **접속**: 노트북/데스크톱/모바일 모두 웹 브라우저로 같은 주소 접속 → **반응형 UI** (모바일은 파일 선택 버튼으로 업로드, 터치 드래그 정렬)
- **ffmpeg**: `.env`의 `FFMPEG_PATH`, `FFPROBE_PATH`로 지정, 없으면 PATH 사용 (로컬 Windows와 서버 Ubuntu에서 코드 동일). 서버는 `apt install ffmpeg`
- **청크 업로드**: 대용량(약 3GB) 대응. 파일을 조각내 올리고 끊겨도 이어받기. 서버는 스트리밍으로 디스크에 저장
- **HTTPS**: nginx(또는 Caddy) 뒤에 배치, 세션 쿠키 `secure`. nginx `client_max_body_size`/타임아웃 상향
- **실행**: uvicorn을 systemd 서비스로 실행, 워커 1개 (작업 상태를 메모리에 보관)
- **도메인**: `upload.example.com` (DNS A 레코드 → VPS IP, Let's Encrypt HTTPS)
- **YouTube OAuth (웹 애플리케이션 유형)**: 관리자 화면의 "유튜브 연결" 버튼 → `/oauth/start` → 구글 인증 → `/oauth/callback`에서 토큰을 `token.json`에 저장 (이후 자동 갱신). 두 경로 모두 관리자 로그인 필요
  - 승인된 리디렉션 URI: `https://upload.example.com/oauth/callback`, `http://localhost:8000/oauth/callback`(로컬 개발)
  - `.env`에 `BASE_URL`(운영/로컬 구분)을 두고 redirect URI를 여기서 생성
- **디스크 정리**: 업로드 성공 시 원본/병합본 즉시 삭제, 실패 시 보관 후 재시도 가능. 디스크 여유는 영상 총합의 2배 이상 필요
- **비용 참고**: 서버 인바운드 트래픽은 무료, 유튜브 업로드(아웃바운드)만 과금 대상 (AWS 기준 3GB당 약 $0.27)

## 9. 확인이 필요한 점 (기본값으로 진행 예정)
- 제목의 날짜: 촬영 시작일 기준(기본) vs 업로드 당일
- "PIR풋" 표기: 붙여쓰기 그대로 `PIR풋`
- 공개 설정 기본값: 비공개
- 병합 후 원본/병합본 파일: 업로드 성공 시 자동 삭제

