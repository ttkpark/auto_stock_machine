"""
시스템 설정 관리

.env 파일을 읽어 환경 변수를 로드합니다.
python-dotenv 패키지가 필요합니다: pip install python-dotenv
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일을 자동으로 로드
load_dotenv(Path(__file__).parent / ".env")


# =============================================
# 타임존 설정
# =============================================
APP_TIMEZONE: str = os.environ.get("APP_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"


def now() -> datetime:
    """APP_TIMEZONE 기준 현재 시각을 반환합니다."""
    try:
        return datetime.now(ZoneInfo(APP_TIMEZONE))
    except Exception:
        return datetime.now(ZoneInfo("Asia/Seoul"))


# =============================================
# 투자 환경 설정
# =============================================
IS_REAL_TRADING: bool = os.environ.get("IS_REAL_TRADING", "False").lower() == "true"

# =============================================
# 매매 전략 설정
# =============================================
# 매수 시 사용할 예수금 비율 (0.9 = 90%)
BUY_BUDGET_RATIO: float = 0.9

# 최소 AI 합의 수: 서로 다른 판단기 중 몇 개가 같은 종목을 추천해야 매수할지.
# 기본 2 = Claude(CLI) + Gemini 둘 다 동의해야 진행 (과반수). .env로 조정 가능.
def _safe_int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


MIN_AI_CONSENSUS: int = _safe_int_env("MIN_AI_CONSENSUS", 2)

# 1회 매수 실행에서 최대 매수 종목 수
MAX_BUY_STOCKS: int = 3

# 자동 매도 조건
TAKE_PROFIT_RATE: float = 5.0    # 수익률 +5% 이상이면 AI에게 매도 판단 요청
STOP_LOSS_RATE: float = -3.0     # 수익률 -3% 이하면 즉시 손절 (AI 판단 없이)

# 동적 손절 (ATR 기반 트레일링 스탑)
TRAILING_STOP_ATR_MULTIPLIER: float = 2.0   # ATR × 이 배수만큼 트레일링 하이에서 하락하면 손절
MARKET_CRASH_THRESHOLD: float = -3.5         # KOSPI/KOSDAQ 전일 대비 이 비율 이하 → 시장 급락 판단
STAGNANT_HOLDING_DAYS: int = 30              # 이 일수 이상 보유 시 장기 횡보 경고 (AI 참고용)

# =============================================
# 스케줄 설정 (내장 스케줄러 사용)
# =============================================
BUY_SCHEDULE: str = "08:30"     # 매수 실행 시각 (평일 장 시작 전)
SELL_SCHEDULE: str = "15:00"    # 매도 실행 시각 (평일 장 마감 전)
STATUS_SCHEDULE: str = "09:00"  # 일일 현황 보고 시각

# =============================================
# 로깅 설정
# =============================================
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """전체 시스템 로깅을 설정합니다."""
    logging.basicConfig(
        level=LOG_LEVEL,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/bot.log", encoding="utf-8"),
        ],
    )


def get_broker():
    """IS_REAL_TRADING 설정에 따라 적절한 브로커를 반환합니다."""
    if IS_REAL_TRADING:
        from brokers import RealBroker
        return RealBroker()
    else:
        from brokers import MockBroker
        return MockBroker()


def get_analyzers() -> list:
    """활성화된 AI 분석기 목록을 반환합니다.

    최대 3개의 독립 판단기를 사용합니다:
      1) Claude(API): CLAUDE_API_KEY 가 있으면 anthropic API로 판단
      2) Claude(CLI): API 키 없이 로컬 `claude` CLI(구독 로그인)로 판단
      3) Gemini(API): GEMINI_API_KEY 가 있으면 경량 모델로 판단
    매수는 MIN_AI_CONSENSUS(기본 2) 만큼 동의해야 진행되며,
    무응답(장애) 엔진은 자동 제외됩니다(살아있는 엔진 수에 맞춰 합의 임계값 조정).
    """
    analyzers = []

    if os.environ.get("CLAUDE_API_KEY"):
        try:
            from analyzers import ClaudeAnalyzer
            analyzers.append(ClaudeAnalyzer())
        except Exception as e:
            logging.warning(f"ClaudeAnalyzer(API) 초기화 실패: {e}")

    if os.environ.get("CLAUDE_CLI_ENABLED", "true").lower() != "false":
        try:
            from analyzers import ClaudeCliAnalyzer
            analyzers.append(ClaudeCliAnalyzer())
        except Exception as e:
            logging.warning(f"ClaudeCliAnalyzer 초기화 실패: {e}")

    if os.environ.get("GEMINI_API_KEY"):
        try:
            from analyzers import GeminiAnalyzer
            analyzers.append(GeminiAnalyzer())
        except Exception as e:
            logging.warning(f"GeminiAnalyzer 초기화 실패: {e}")

    if not analyzers:
        raise RuntimeError(
            "활성화된 AI 분석기가 없습니다. CLAUDE_API_KEY 또는 GEMINI_API_KEY를 설정하거나 "
            "claude CLI 로그인(claude login)을 확인해 주세요."
        )
    return analyzers
