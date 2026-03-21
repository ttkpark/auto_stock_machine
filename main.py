"""
다중 AI 기반 주식 자동매매 봇 - 메인 실행 파일

실행 방법:
    python main.py --mode buy    # 매수 로직 실행
    python main.py --mode sell   # 매도 로직 실행
    python main.py --mode status # 현재 계좌 현황 텔레그램 전송

crontab 예시 (우분투):
    30 8 * * 1-5  /path/to/venv/bin/python /path/to/main.py --mode buy
    0 15 * * 1-5  /path/to/venv/bin/python /path/to/main.py --mode sell
"""
import argparse
import logging
import sys
from pathlib import Path

import config
from notifiers import TelegramNotifier
from utils import StockValidator, DecisionMaker

# logs 디렉토리 생성
Path("logs").mkdir(exist_ok=True)
config.setup_logging()
logger = logging.getLogger(__name__)


def run_buy_logic(broker, analyzers, validator: StockValidator, notifier: TelegramNotifier):
    """매수 로직: AI 다수결 종목 발굴 → 환각 검증 → 매수 주문"""
    logger.info("=" * 50)
    logger.info("[매수 로직] 시작")

    balance = broker.get_balance()
    logger.info(f"주문 가능 예수금: {balance:,}원")

    if balance < 10_000:
        msg = f"예수금 부족 ({balance:,}원). 매수를 건너뜁니다."
        logger.warning(msg)
        notifier.send(f"[매수 스킵] {msg}")
        return

    # AI 에게 매수 종목 추천 요청
    recommendations = []
    for analyzer in analyzers:
        rec = analyzer.recommend_buy(balance=balance)
        if rec:
            logger.info(f"[{rec.ai_model}] 추천: {rec.stock_name} - {rec.reason}")
            recommendations.append(rec)

    # 다수결 합의 종목 선정
    decision_maker = DecisionMaker(min_consensus=config.MIN_AI_CONSENSUS)
    agreed_stock_name = decision_maker.find_buy_consensus(recommendations)

    if not agreed_stock_name:
        msg = "AI 합의 종목 없음. 오늘은 매수하지 않습니다."
        logger.info(msg)
        notifier.send(f"[매수 스킵] {msg}")
        return

    # 환각 방지: KRX 실제 상장 종목인지 검증
    ticker = validator.verify_and_get_code(agreed_stock_name)
    if not ticker:
        msg = f"'{agreed_stock_name}'은 KRX 미상장 종목 또는 오류입니다. (AI 환각 차단)"
        logger.warning(msg)
        notifier.send(f"[매수 차단] {msg}")
        return

    # 매수 수량 계산
    current_price = broker.get_current_price(ticker)
    if not current_price or current_price <= 0:
        msg = f"'{agreed_stock_name}' 현재가 조회 실패. 매수를 중단합니다."
        logger.error(msg)
        notifier.send(f"[매수 오류] {msg}")
        return

    budget = int(balance * config.BUY_BUDGET_RATIO)
    qty = budget // current_price

    if qty < 1:
        msg = (
            f"예수금 {budget:,}원으로 {agreed_stock_name}({current_price:,}원) "
            f"1주 구매 불가. 매수를 건너뜁니다."
        )
        logger.warning(msg)
        notifier.send(f"[매수 스킵] {msg}")
        return

    # 매수 주문 실행
    ai_list = [r.ai_model for r in recommendations if r.stock_name == agreed_stock_name]
    reasons = [r.reason for r in recommendations if r.stock_name == agreed_stock_name]
    logger.info(f"매수 주문: {agreed_stock_name} ({ticker}) {qty}주 @ {current_price:,}원")

    success = broker.buy_order(ticker=ticker, qty=qty)
    if success:
        notifier.notify_buy_order(agreed_stock_name, ticker, qty, current_price)
        detail_msg = (
            f"\n추천 AI: {', '.join(ai_list)}\n"
            f"이유: {' / '.join(reasons)}"
        )
        notifier.send(detail_msg)
    else:
        notifier.send(f"[매수 실패] {agreed_stock_name} ({ticker}) {qty}주 주문 실패")

    logger.info("[매수 로직] 완료")


def run_sell_logic(broker, analyzers, notifier: TelegramNotifier):
    """매도 로직: 보유 종목 순회 → 손절/AI 판단 → 매도 주문"""
    logger.info("=" * 50)
    logger.info("[매도 로직] 시작")

    holdings = broker.get_holdings()
    if not holdings:
        logger.info("보유 종목 없음. 매도 로직을 건너뜁니다.")
        return

    decision_maker = DecisionMaker()

    for holding in holdings:
        name = holding["name"]
        ticker = holding["ticker"]
        qty = holding["qty"]
        avg_price = holding["avg_price"]
        current_price = holding["current_price"]
        profit_rate = holding["profit_rate"]

        logger.info(
            f"보유 종목 검토: {name} ({ticker}) "
            f"{qty}주 | 수익률 {profit_rate:.1f}%"
        )

        # 손절 조건: AI 판단 없이 즉시 매도
        if profit_rate <= config.STOP_LOSS_RATE:
            logger.warning(f"[손절] {name} 수익률 {profit_rate:.1f}% - 즉시 매도")
            success = broker.sell_order(ticker=ticker, qty=qty)
            if success:
                notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                notifier.send(f"[손절 매도] {name} | 손실률 {profit_rate:.1f}%")
            continue

        # 익절 조건에 도달했을 때만 AI에게 판단 요청
        if profit_rate >= config.TAKE_PROFIT_RATE:
            decisions = []
            for analyzer in analyzers:
                decision = analyzer.decide_sell(
                    stock_name=name,
                    ticker=ticker,
                    qty=qty,
                    avg_price=avg_price,
                    current_price=current_price,
                    profit_rate=profit_rate,
                )
                logger.info(
                    f"[{decision.ai_model}] {name} → {decision.action}: {decision.reason}"
                )
                decisions.append(decision)

            final_decision = decision_maker.decide_sell_by_vote(decisions)
            logger.info(f"최종 결정: {name} → {final_decision.action}")

            if final_decision.action == "매도":
                success = broker.sell_order(ticker=ticker, qty=qty)
                if success:
                    notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                    notifier.send(f"[매도 이유] {final_decision.reason}")
        else:
            logger.info(f"{name}: 매도 조건 미달 ({profit_rate:.1f}%) - 보유 유지")

    logger.info("[매도 로직] 완료")


def run_status(broker, notifier: TelegramNotifier):
    """현재 계좌 현황을 텔레그램으로 전송합니다."""
    logger.info("[현황 보고] 시작")
    balance = broker.get_balance()
    holdings = broker.get_holdings()
    notifier.notify_daily_summary(balance=balance, holdings=holdings)
    logger.info("[현황 보고] 완료")


def main():
    parser = argparse.ArgumentParser(description="다중 AI 주식 자동매매 봇")
    parser.add_argument(
        "--mode",
        choices=["buy", "sell", "status"],
        required=True,
        help="실행 모드: buy(매수) / sell(매도) / status(현황)",
    )
    args = parser.parse_args()

    trading_mode = "실전투자" if config.IS_REAL_TRADING else "모의투자"
    logger.info(f"봇 시작 | 모드: {args.mode} | 환경: {trading_mode}")

    try:
        broker = config.get_broker()
        analyzers = config.get_analyzers()
        notifier = TelegramNotifier()
        validator = StockValidator()

        if args.mode == "buy":
            run_buy_logic(broker, analyzers, validator, notifier)
        elif args.mode == "sell":
            run_sell_logic(broker, analyzers, notifier)
        elif args.mode == "status":
            run_status(broker, notifier)

    except Exception as e:
        logger.error(f"치명적 오류 발생: {e}", exc_info=True)
        try:
            TelegramNotifier().notify_error("main()", e)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
