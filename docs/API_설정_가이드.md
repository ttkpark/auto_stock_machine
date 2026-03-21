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

## 2. Google Gemini API

### 발급 방법
1. 브라우저에서 접속:
   **https://aistudio.google.com/app/apikey**
2. Google 계정으로 로그인
3. 파란색 **[Create API key]** 버튼 클릭
4. 프로젝트 선택 (없으면 새 프로젝트 생성) → **[Create API key in existing project]**
5. 생성된 키(`AIzaSy...` 형태) 복사

### 무료 할당량
- Gemini 1.5 Flash 기준: 분당 15회, 하루 1,500회 무료
- 자동매매 봇 수준(하루 2~5회 호출)에서는 무료 티어로 충분

### .env 파일 입력 예시
```
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 3. Anthropic Claude API

### 발급 방법
1. 브라우저에서 접속:
   **https://console.anthropic.com**
2. 계정 생성 또는 로그인
3. 좌측 메뉴 → **API Keys** → **[Create Key]**
4. 키 이름 입력 → 생성된 키(`sk-ant-...` 형태) 즉시 복사
   (이 화면을 벗어나면 다시 볼 수 없으므로 반드시 저장!)

### 비용 안내
- Claude 3.5 Haiku 기준: 입력 1M 토큰당 $0.80, 출력 1M 토큰당 $4.00
- 하루 2~5회 호출 기준 월 1~2달러 수준

### .env 파일 입력 예시
```
CLAUDE_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 4. Telegram Bot

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

## 5. 최종 .env 파일 완성 예시

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

# AI
GEMINI_API_KEY=AIzaSyxxxxxxxxxx
CLAUDE_API_KEY=sk-ant-xxxxxxxxxx

# 텔레그램
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxx
TELEGRAM_CHAT_ID=123456789

# 모의투자로 시작 (테스트 완료 후 True로 변경)
IS_REAL_TRADING=False
```

---

## 6. 우분투 crontab 자동 실행 설정

모든 테스트가 완료된 후, 우분투에서 아래 명령으로 crontab을 설정합니다.

```bash
crontab -e
```

아래 내용을 추가합니다 (경로는 실제 설치 경로로 변경):

```
# 평일(월-금) 오전 8:30 - 매수 로직
30 8 * * 1-5 /home/ubuntu/auto_stock_machine/venv/bin/python /home/ubuntu/auto_stock_machine/main.py --mode buy >> /home/ubuntu/auto_stock_machine/logs/cron.log 2>&1

# 평일(월-금) 오후 3:00 - 매도 로직
0 15 * * 1-5 /home/ubuntu/auto_stock_machine/venv/bin/python /home/ubuntu/auto_stock_machine/main.py --mode sell >> /home/ubuntu/auto_stock_machine/logs/cron.log 2>&1

# 매일 오전 9:00 - 일일 현황 보고
0 9 * * 1-5 /home/ubuntu/auto_stock_machine/venv/bin/python /home/ubuntu/auto_stock_machine/main.py --mode status >> /home/ubuntu/auto_stock_machine/logs/cron.log 2>&1
```

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

`http://127.0.0.1:5000`

로그인 후 `IS_REAL_TRADING` 포함 주요 `.env` 값을 수정/저장할 수 있습니다.
