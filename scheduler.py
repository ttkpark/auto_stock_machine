"""
내장 스케줄러 - 윈도우/리눅스 모두 동작

crontab 없이 Python 프로세스 내에서 평일 지정 시각에
매수/매도/현황 보고를 자동 실행합니다.

실행 방법:
    python main.py --mode schedule
"""
import logging
import time
from datetime import datetime

import schedule

from bot_service import execute_mode

logger = logging.getLogger(__name__)


def _is_weekday() -> bool:
    """오늘이 평일(월~금)인지 확인합니다."""
    return datetime.now().weekday() < 5  # 0=월 ~ 4=금


def _run_if_weekday(mode: str) -> None:
    """평일에만 해당 모드를 실행합니다."""
    if not _is_weekday():
        logger.info(f"[스케줄러] 주말이므로 {mode} 실행을 건너뜁니다.")
        return

    logger.info(f"[스케줄러] {mode} 자동 실행 시작")
    try:
        result = execute_mode(mode)
        status = "성공" if result["ok"] else "실패"
        logger.info(f"[스케줄러] {mode} 실행 {status}")
    except Exception as e:
        logger.error(f"[스케줄러] {mode} 실행 중 오류: {e}", exc_info=True)


def start_scheduler(
    buy_time: str = "08:30",
    sell_time: str = "15:00",
    status_time: str = "09:00",
) -> None:
    """
    스케줄러를 시작합니다. 이 함수는 블로킹이며 Ctrl+C로 종료합니다.

    Args:
        buy_time: 매수 실행 시각 (HH:MM)
        sell_time: 매도 실행 시각 (HH:MM)
        status_time: 현황 보고 시각 (HH:MM)
    """
    schedule.every().day.at(buy_time).do(_run_if_weekday, mode="buy")
    schedule.every().day.at(sell_time).do(_run_if_weekday, mode="sell")
    schedule.every().day.at(status_time).do(_run_if_weekday, mode="status")

    logger.info("=" * 50)
    logger.info("[스케줄러] 자동매매 스케줄러 시작")
    logger.info(f"  매수: 평일 {buy_time}")
    logger.info(f"  매도: 평일 {sell_time}")
    logger.info(f"  현황: 평일 {status_time}")
    logger.info("  종료: Ctrl+C")
    logger.info("=" * 50)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("[스케줄러] 사용자에 의해 종료됨")
