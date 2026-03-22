# Auto Stock Machine

다중 AI 교차 검증 기반 한국 주식 자동매매 봇

여러 AI 모델(Gemini, Claude 등)의 다수결 합의를 통해 신뢰도 높은 종목을 발굴하고,  
우분투 서버에서 24시간 무중단으로 텔레그램 알림과 함께 자동 매매를 수행합니다.

---

## 우분투 서버 원라인 설치

아래 명령 하나로 우분투 서버에 설치할 수 있습니다.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/auto_stock_machine/main/install_ubuntu.sh) \
  --repo-url https://github.com/YOUR_USERNAME/auto_stock_machine.git
```

설치 스크립트가 자동으로 수행하는 작업:

- `git`, `python3`, `python3-venv`, `python3-pip` 설치
- 프로젝트 클론/업데이트 (`~/auto_stock_machine`)
- 가상환경 생성 (`.venv`) 및 `requirements.txt` 설치
- `.env.example` 기반 `.env` 생성(기존 `.env`가 있으면 유지)
- `logs/`, `data/` 런타임 디렉터리 생성

설치 후 필수 작업:

```bash
cd ~/auto_stock_machine
nano .env
```

`.env` 에 실제 API 키를 입력한 뒤 아래 명령으로 연동을 확인하세요.

```bash
~/auto_stock_machine/.venv/bin/python ~/auto_stock_machine/main.py --mode status
```

---

## 주요 기능

- **다중 AI 교차 검증**: Gemini, Claude 등 복수의 AI가 동의한 종목만 매수
- **AI 환각(Hallucination) 방지**: KRX 전체 상장 종목 DB와 대조하여 존재하지 않는 종목 차단
- **플러그 앤 플레이 브로커**: `.env` 파일의 값 하나로 모의투자 ↔ 실전투자 즉시 전환
- **텔레그램 실시간 알림**: 매수/매도 체결, 오류, 일일 현황 모두 텔레그램으로 수신
- **자동 손절/익절**: 수익률 기준(-3% 손절, +5% 이상 AI 판단)으로 자동 대응

---

## 시스템 구조

```
auto_stock_machine/
├── main.py                  메인 실행 파일
├── config.py                시스템 설정
├── requirements.txt
├── .env                     API 키 (직접 생성, Git 제외)
├── .env.example             API 키 입력 양식
│
├── brokers/                 증권사 통신 (한국투자증권)
│   ├── base_broker.py       추상 인터페이스
│   ├── mock_broker.py       모의투자
│   └── real_broker.py       실전투자
│
├── analyzers/               AI 분석기
│   ├── base_analyzer.py     추상 인터페이스
│   ├── gemini_analyzer.py   Google Gemini
│   └── claude_analyzer.py   Anthropic Claude
│
├── notifiers/
│   └── telegram_notifier.py 텔레그램 알림
│
├── utils/
│   ├── stock_validator.py   KRX 종목 검증 (환각 방지)
│   └── decision_maker.py    다수결 의사결정
│
└── docs/
    ├── 개발_명세서.md        전체 설계 문서
    └── API_설정_가이드.md    외부 API 등록 방법
```

---

## 빠른 시작

### 1단계: 저장소 클론 및 패키지 설치

```bash
git clone https://github.com/YOUR_USERNAME/auto_stock_machine.git
cd auto_stock_machine

# 가상환경 생성 (권장)
python3 -m venv venv
source venv/bin/activate   # 우분투
# venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

> 우분투 서버에서 빠르게 설치하려면 위 수동 절차 대신 `우분투 서버 원라인 설치` 섹션을 사용하세요.

### 2단계: API 키 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 각 항목에 실제 API 키를 입력합니다.  
각 서비스의 키 발급 방법은 **[docs/API_설정_가이드.md](docs/API_설정_가이드.md)** 를 참고하세요.

| 필요한 키 | 발급처 | 필수 여부 |
|---|---|---|
| 한국투자증권 모의투자 App Key/Secret | [KIS Developers](https://apiportal.koreainvestment.com) | 필수 |
| 한국투자증권 실전투자 App Key/Secret | [KIS Developers](https://apiportal.koreainvestment.com) | 실전 전환 시 |
| Google Gemini API Key | [Google AI Studio](https://aistudio.google.com/app/apikey) | 필수 |
| Anthropic Claude API Key | [Anthropic Console](https://console.anthropic.com) | 선택 (권장) |
| Telegram Bot Token | 텔레그램 @BotFather | 필수 |
| Telegram Chat ID | 텔레그램 봇과 대화 후 조회 | 필수 |

### 3단계: 텔레그램 연동 확인

```bash
python main.py --mode status
```

텔레그램으로 계좌 현황 메시지가 수신되면 연동 성공입니다.

### (선택) 웹에서 .env 설정 관리

`.env` 에 `WEB_ADMIN_PASSWORD`를 설정한 뒤, 아래 명령으로 웹 관리 페이지를 실행할 수 있습니다.

```bash
python web_admin.py
```

브라우저에서 `http://127.0.0.1:8004` 접속 후 로그인하면 (기본은 모든 인터페이스 `0.0.0.0:8004`에 바인드)  
아래 기능을 한 번에 사용할 수 있습니다.

- 대시보드: 현재 투자 모드, 예수금, 보유 종목, 수익률, 오늘의 판단 로그
- 모드 전환: `IS_REAL_TRADING` 모의/실전 스위칭
- 환경설정: 주요 `.env` 변수 수정/저장
- 액션실행: `buy/sell/status` 수동 실행
- 서버상태: 파이썬 버전, 업타임, 로그 갱신 시각 확인

### 4단계: 매매 로직 테스트 (모의투자)

```bash
# 매수 로직 실행
python main.py --mode buy

# 매도 로직 실행
python main.py --mode sell
```

> `.env` 파일의 `IS_REAL_TRADING=False` 상태에서는 모의투자 계좌로만 동작합니다.

### 5단계: 우분투 crontab 자동화

```bash
crontab -e
```

```
# 평일 오전 8:30 매수
30 8 * * 1-5 /path/to/venv/bin/python /path/to/main.py --mode buy >> /path/to/logs/cron.log 2>&1

# 평일 오후 3:00 매도
0 15 * * 1-5 /path/to/venv/bin/python /path/to/main.py --mode sell >> /path/to/logs/cron.log 2>&1
```

자세한 crontab 설정은 **[docs/API_설정_가이드.md](docs/API_설정_가이드.md)** 를 참고하세요.

---

## 실전 투자 전환

모의투자에서 충분히 테스트한 후 `.env` 파일에서 단 한 줄만 변경합니다.

```dotenv
# 변경 전
IS_REAL_TRADING=False

# 변경 후 (실제 계좌가 움직입니다!)
IS_REAL_TRADING=True
```

---

## 보안 주의사항

- `.env` 파일은 절대 Git에 올리지 마세요. `.gitignore`에 이미 등록되어 있습니다.
- API 키가 외부에 노출되면 즉시 해당 서비스에서 키를 폐기하고 재발급하세요.
- 실전투자 전환 전 반드시 모의투자에서 1주일 이상 안정적으로 작동하는지 확인하세요.

---

## 개발 문서

| 문서 | 설명 |
|---|---|
| [개발 명세서](docs/개발_명세서.md) | 전체 시스템 설계, 매매 전략, 체크리스트 |
| [API 설정 가이드](docs/API_설정_가이드.md) | 외부 서비스 키 발급 및 등록 방법 |

---

## 기술 스택

- **Python 3.10+**
- **google-generativeai** - Gemini AI 연동
- **anthropic** - Claude AI 연동
- **FinanceDataReader** - KRX 종목 데이터
- **python-telegram-bot** - 텔레그램 알림
- **requests** - 한국투자증권 REST API 통신
- **python-dotenv** - 환경 변수 관리

---

## 면책 조항

이 프로젝트는 학습 및 연구 목적으로 제작되었습니다.  
AI의 투자 판단은 항상 틀릴 수 있으며, 실제 투자 손실에 대한 책임은 사용자 본인에게 있습니다.  
반드시 본인이 감당할 수 있는 금액으로만 테스트하세요.
