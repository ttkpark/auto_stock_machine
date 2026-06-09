# 외부 API 설정 가이드

> 이 가이드는 `.env` 파일을 채우기 위한 단계별 외부 서비스 등록 방법입니다.

---

## 1. 한국투자증권 KIS Developers API

### 계좌 개설
1. 스마트폰에서 **'한국투자' 앱** 설치
2. 앱 내에서 **비대면 종합계좌 개설** (본인 인증 필요)
3. 시드머니(10만 원) 입금

### 모의투자 가입
1. '한국투자' 앱 → 메뉴 → **모의투자** 서비스 가입
2. 가입 완료 후 모의투자 전용 계좌번호 확인

### API 키 발급
1. PC 브라우저에서 접속:
   **https://apiportal.koreainvestment.com**
2. 우측 상단 **로그인** (한국투자증권 공인인증서 또는 앱 QR 로그인)
3. 상단 메뉴 → **API 서비스 신청**
4. **[실전투자]** 와 **[모의투자]** 각각 신청
5. 신청 완료 후 **내 정보 → APP KEY 관리** 에서 키 확인

### 계좌번호 형식 안내
- 계좌번호는 보통 `XXXXXXXX-XX` 형식 (8자리 + 2자리)
- `.env` 에는 하이픈 없이 10자리로 입력: `KIS_MOCK_ACCOUNT_NUMBER=XXXXXXXXXX`

### .env 파일 입력 예시
```
KIS_MOCK_APP_KEY=PSxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_MOCK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_MOCK_ACCOUNT_NUMBER=5000000000

KIS_REAL_APP_KEY=PSxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_REAL_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_REAL_ACCOUNT_NUMBER=5000000000
```

---

## 2. Anthropic Claude (CLI, API 키 불필요)

Claude 판단은 Anthropic API 키 대신 **로컬 `claude` CLI(Claude Code)**를 구독 로그인으로 호출합니다.
별도 API 키 발급/결제 없이 구독 계정만으로 동작합니다.

### 설치 및 로그인 (우분투, 봇 실행 사용자로 진행)
```bash
# 1) Node.js 설치
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# 2) claude CLI 설치
npm install -g @anthropic-ai/claude-code

# 3) 로그인 (브라우저 인증 → ~/.claude 에 토큰 저장)
claude login

# 4) 동작 확인
echo 'reply with {"ok":true} only' | claude -p --model sonnet
```

> systemd 서비스는 설치한 사용자(`User=`)로 동작하므로 **그 사용자로 `claude login`** 해야 합니다.

### .env 파일 입력 예시
```
# API 키 없음. CLI 사용 여부/모델만 지정.
CLAUDE_CLI_ENABLED=true
CLAUDE_CLI_MODEL=sonnet
```

### 참고
- 가드레일(개인 금융자문 거부) 때문에 CLI 경로는 동일한 매매 규칙을 **익명화 백테스트 프레이밍**으로 재구성해 호출합니다.
- OpenAI ChatGPT를 추가로 교차검증에 쓰려면 `OPENAI_API_KEY`(선택)를 입력하세요.

---

## 3. Telegram Bot

### BotFather로 봇 생성
1. 텔레그램 앱에서 **@BotFather** 검색 → 대화 시작
2. `/newbot` 명령어 전송
3. 봇 이름 입력 (예: `내주식봇`)
4. 봇 유저네임 입력 - 반드시 `bot`으로 끝나야 함 (예: `my_stock_auto_bot`)
5. BotFather가 **HTTP API 토큰** 발급 (`1234567890:AAxxxx...` 형태) → 복사 보관

### 본인 Chat ID 확인 방법
1. 방금 만든 봇을 검색하여 **대화 시작 (`/start`)**
2. 브라우저에서 아래 URL 접속 (본인 토큰으로 교체):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. JSON 응답에서 `"chat"` → `"id"` 값을 복사
   ```json
   "chat": {
     "id": 123456789,
     ...
   }
   ```

### .env 파일 입력 예시
```
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

---

## 4. 최종 .env 파일 완성 예시

`.env.example` 을 복사하여 `.env` 파일을 만들고 아래와 같이 채웁니다.

```bash
# 우분투 터미널에서
cp .env.example .env
nano .env   # 또는 vi .env
```

```dotenv
# 한국투자증권
KIS_MOCK_APP_KEY=PSxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_MOCK_APP_SECRET=xxxx...
KIS_MOCK_ACCOUNT_NUMBER=5000000000

KIS_REAL_APP_KEY=PSxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_REAL_APP_SECRET=xxxx...
KIS_REAL_ACCOUNT_NUMBER=5000000000

# AI (Claude는 API 키 없이 'claude login' CLI 사용)
CLAUDE_CLI_ENABLED=true
CLAUDE_CLI_MODEL=sonnet
# OPENAI_API_KEY=sk-xxxxxxxxxx   # 선택

# 텔레그램
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxx
TELEGRAM_CHAT_ID=123456789

# 모의투자로 시작 (테스트 완료 후 True로 변경)
IS_REAL_TRADING=False
```

---

## 6. 자동매매 스케줄러 실행

모든 테스트가 완료된 후, 아래 명령으로 내장 스케줄러를 시작합니다.

```bash
python main.py --mode schedule
```

스케줄러가 평일에 자동으로 매수(08:30), 매도(15:00), 현황 보고(09:00)를 실행합니다.
실행 시각은 `config.py`의 `BUY_SCHEDULE`, `SELL_SCHEDULE`, `STATUS_SCHEDULE`에서 변경할 수 있습니다.

윈도우/리눅스 모두 동일하게 동작하며, `Ctrl+C`로 종료합니다.

---

## 7. 웹으로 환경 변수 관리 (선택)

`.env` 에 아래 값을 추가합니다.

```dotenv
WEB_ADMIN_PASSWORD=원하는_로그인_비밀번호
WEB_ADMIN_SESSION_SECRET=
```

웹 관리자 실행:

```bash
python web_admin.py
```

브라우저 접속:

`http://127.0.0.1:8004` (기본 바인드: `0.0.0.0:8004`, 같은 네트워크의 다른 기기는 `http://<서버_IP>:8004`)

로그인 후 `IS_REAL_TRADING` 포함 주요 `.env` 값을 수정/저장할 수 있습니다.
