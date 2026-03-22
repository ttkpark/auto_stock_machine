"""
다중 AI 기반 주식 자동매매 봇 - 통합 실행 파일

실행 방법:
    python main.py --mode buy       # 매수 로직 실행
    python main.py --mode sell      # 매도 로직 실행
    python main.py --mode status    # 현재 계좌 현황 텔레그램 전송
    python main.py --mode schedule  # 내장 스케줄러 (평일 자동 매매)
    python main.py --mode web       # 웹 관리자 + 백엔드 통합 실행
"""
import argparse
import sys

from bot_service import execute_mode


def main():
    parser = argparse.ArgumentParser(description="다중 AI 주식 자동매매 봇")
    parser.add_argument(
        "--mode",
        choices=["buy", "sell", "status", "schedule", "web"],
        required=True,
        help="실행 모드: buy(매수) / sell(매도) / status(현황) / schedule(자동) / web(웹 관리자)",
    )
    args = parser.parse_args()

    if args.mode == "web":
        from web_admin import run_web_admin
        run_web_admin()
        return

    if args.mode == "schedule":
        import config
        from scheduler import start_scheduler
        config.setup_logging()
        start_scheduler(
            buy_time=config.BUY_SCHEDULE,
            sell_time=config.SELL_SCHEDULE,
            status_time=config.STATUS_SCHEDULE,
        )
        return

    result = execute_mode(args.mode)
    sys.exit(result["returncode"])


if __name__ == "__main__":
    main()
