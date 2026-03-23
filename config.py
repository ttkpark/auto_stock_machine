"""
시스템 설정 관리

.env 파일을 읽어 환경 변수를 로드합니다.
python-dotenv 패키지가 필요합니다: pip install python-dotenv
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일을 자동으로 로드
load_dotenv(Path(__file__).parent / ".env")


# =============================================
# 투자 환경 설정
# =============================================
IS_REAL_TRADING: bool = os.environ.get("IS_REAL_TRADING", "False").lower() == "true"

# =============================================
# 매매 전략 설정
# =============================================
# 매수 시 사용할 예수금 비율 (0.9 = 90%)
BUY_BUDGET_RATIO: float = 0.9

# 최소 AI 합의 수 (추천 1개 이상이면 매수 후보 인정)
MIN_AI_CONSENSUS: int = 1

# 1회 매수 실행에서 최대 매수 종목 수
MAX_BUY_STOCKS: int = 3

# 자동 매도 조건
TAKE_PROFIT_RATE: float = 5.0    # 수익률 +5% 이상이면 AI에게 매도 판단 요청
STOP_LOSS_RATE: float = -3.0     # 수익률 -3% 이하면 즉시 손절 (AI 판단 없이)

# 동적 손절 (ATR 기반 트레일링 스탑)
TRAILING_STOP_ATR_MULTIPLIER: float = 2.0   # ATR × 이 배수만큼 트레일링 하이에서 하락하면 손절
MARKET_CRASH_THRESHOLD: float = -2.0         # KOSPI/KOSDAQ 전일 대비 이 비율 이하 → 시장 급락 판단
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
    """활성화된 AI 분석기 목록을 반환합니다."""
    analyzers = []

    if os.environ.get("GEMINI_API_KEY"):
        try:
            from analyzers import GeminiAnalyzer
            analyzers.append(GeminiAnalyzer())
        except Exception as e:
            logging.warning(f"GeminiAnalyzer 초기화 실패: {e}")

    if os.environ.get("CLAUDE_API_KEY"):
        try:
            from analyzers import ClaudeAnalyzer
            analyzers.append(ClaudeAnalyzer())
        except Exception as e:
            logging.warning(f"ClaudeAnalyzer 초기화 실패: {e}")

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from analyzers import OpenAIAnalyzer
            analyzers.append(OpenAIAnalyzer())
        except Exception as e:
            logging.warning(f"OpenAIAnalyzer 초기화 실패: {e}")

    if not analyzers:
        raise RuntimeError(
            "활성화된 AI 분석기가 없습니다. "
            ".env 파일에 GEMINI_API_KEY, CLAUDE_API_KEY, OPENAI_API_KEY 중 하나 이상을 설정해 주세요."
        )
    return analyzers
