"""
매매 백엔드 공용 실행 서비스.

CLI(main.py)와 웹(web_admin.py)에서 동일한 실행 경로를 사용합니다.
"""
import importlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Literal

import config as config_module
from notifiers import TelegramNotifier
from utils import DecisionMaker, StockValidator

BotMode = Literal["buy", "sell", "status"]
TRACE_PATH = Path("data/ai_traces.jsonl")

logger = logging.getLogger(__name__)


class TraceRecorder:
    """AI 판단 과정을 구조화해 저장합니다."""

    def __init__(self, mode: BotMode, run_id: str, trading_mode: str):
        self.mode = mode
        self.run_id = run_id
        self.trading_mode = trading_mode

    def record(self, event_type: str, **payload: Any) -> None:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "run_id": self.run_id,
            "mode": self.mode,
            "trading_mode": self.trading_mode,
            "event_type": event_type,
            "payload": payload,
        }
        with TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _prepare_runtime():
    """
    실행 시점마다 .env 변경 사항이 반영되도록 config 모듈을 재로드합니다.
    """
    cfg = importlib.reload(config_module)
    Path("logs").mkdir(exist_ok=True)
    cfg.setup_logging()
    return cfg


def run_buy_logic(
    broker,
    analyzers,
    validator: StockValidator,
    notifier: TelegramNotifier,
    cfg,
    trace: TraceRecorder,
):
    """매수 로직: AI 다수결 종목 발굴 → 환각 검증 → 매수 주문"""
    logger.info("=" * 50)
    logger.info("[매수 로직] 시작")

    balance = broker.get_balance()
    logger.info(f"주문 가능 예수금: {balance:,}원")
    trace.record("buy_balance", balance=balance, min_required=10000)

    if balance < 10_000:
        msg = f"예수금 부족 ({balance:,}원). 매수를 건너뜁니다."
        logger.warning(msg)
        notifier.send(f"[매수 스킵] {msg}")
        trace.record("buy_skipped", reason=msg)
        return

    recommendations = []
    for analyzer in analyzers:
        analyzer_name = analyzer.__class__.__name__
        rec = analyzer.recommend_buy(balance=balance)
        if rec:
            logger.info(f"[{rec.ai_model}] 추천: {rec.stock_name} - {rec.reason}")
            recommendations.append(rec)
            trace.record(
                "buy_recommendation",
                analyzer=analyzer_name,
                ai_model=rec.ai_model,
                stock_name=rec.stock_name,
                reason=rec.reason,
            )
        else:
            trace.record(
                "buy_recommendation_empty",
                analyzer=analyzer_name,
                reason=getattr(analyzer, "last_recommendation_error", "") or "추천 없음",
            )

    decision_maker = DecisionMaker(min_consensus=cfg.MIN_AI_CONSENSUS)
    max_buy_stocks = int(getattr(cfg, "MAX_BUY_STOCKS", 1))
    agreed_stock_names = decision_maker.find_buy_consensus_candidates(
        recommendations,
        max_stocks=max_buy_stocks,
    )
    trace.record(
        "buy_consensus_result",
        min_consensus=cfg.MIN_AI_CONSENSUS,
        max_buy_stocks=max_buy_stocks,
        agreed_stock_names=agreed_stock_names,
    )

    if not agreed_stock_names:
        msg = "AI 합의 종목 없음. 오늘은 매수하지 않습니다."
        logger.info(msg)
        notifier.send(f"[매수 스킵] {msg}")
        trace.record("buy_skipped", reason=msg)
        return

    budget = int(balance * cfg.BUY_BUDGET_RATIO)
    budget_per_stock = budget // max(1, len(agreed_stock_names))
    trace.record(
        "buy_budget_plan",
        total_budget=budget,
        stock_count=len(agreed_stock_names),
        budget_per_stock=budget_per_stock,
    )

    bought_count = 0
    for agreed_stock_name in agreed_stock_names:
        ticker = validator.verify_and_get_code(agreed_stock_name)
        if not ticker:
            msg = f"'{agreed_stock_name}'은 KRX 미상장 종목 또는 오류입니다. (AI 환각 차단)"
            logger.warning(msg)
            notifier.send(f"[매수 차단] {msg}")
            trace.record("buy_blocked", stock_name=agreed_stock_name, reason=msg)
            continue

        current_price = broker.get_current_price(ticker)
        if not current_price or current_price <= 0:
            msg = f"'{agreed_stock_name}' 현재가 조회 실패. 매수를 중단합니다."
            logger.error(msg)
            notifier.send(f"[매수 오류] {msg}")
            trace.record(
                "buy_error",
                stock_name=agreed_stock_name,
                ticker=ticker,
                reason=msg,
            )
            continue

        qty = budget_per_stock // current_price
        trace.record(
            "buy_quantity_calculated",
            stock_name=agreed_stock_name,
            ticker=ticker,
            current_price=current_price,
            budget=budget_per_stock,
            qty=qty,
        )
        if qty < 1:
            msg = (
                f"예산 {budget_per_stock:,}원으로 {agreed_stock_name}({current_price:,}원) "
                f"1주 구매 불가. 해당 종목 매수를 건너뜁니다."
            )
            logger.warning(msg)
            notifier.send(f"[매수 스킵] {msg}")
            trace.record("buy_skipped", stock_name=agreed_stock_name, reason=msg)
            continue

        ai_list = [r.ai_model for r in recommendations if r.stock_name == agreed_stock_name]
        reasons = [r.reason for r in recommendations if r.stock_name == agreed_stock_name]
        logger.info(f"매수 주문: {agreed_stock_name} ({ticker}) {qty}주 @ {current_price:,}원")

        success = broker.buy_order(ticker=ticker, qty=qty)
        if success:
            bought_count += 1
            notifier.notify_buy_order(agreed_stock_name, ticker, qty, current_price)
            detail_msg = (
                f"\n추천 AI: {', '.join(ai_list)}\n"
                f"이유: {' / '.join(reasons)}"
            )
            notifier.send(detail_msg)
        else:
            notifier.send(f"[매수 실패] {agreed_stock_name} ({ticker}) {qty}주 주문 실패")

        trace.record(
            "buy_order_result",
            success=success,
            stock_name=agreed_stock_name,
            ticker=ticker,
            qty=qty,
            current_price=current_price,
            ai_models=ai_list,
            reasons=reasons,
            broker_error=getattr(broker, "last_order_error", ""),
        )

    trace.record(
        "buy_summary",
        target_count=len(agreed_stock_names),
        bought_count=bought_count,
    )

    logger.info("[매수 로직] 완료")


def run_sell_logic(broker, analyzers, notifier: TelegramNotifier, cfg, trace: TraceRecorder):
    """매도 로직: 보유 종목 순회 → 손절/AI 판단 → 매도 주문"""
    logger.info("=" * 50)
    logger.info("[매도 로직] 시작")

    holdings = broker.get_holdings()
    trace.record("sell_holdings_loaded", count=len(holdings))
    if not holdings:
        logger.info("보유 종목 없음. 매도 로직을 건너뜁니다.")
        trace.record("sell_skipped", reason="보유 종목 없음")
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
        trace.record(
            "sell_holding_checked",
            stock_name=name,
            ticker=ticker,
            qty=qty,
            avg_price=avg_price,
            current_price=current_price,
            profit_rate=profit_rate,
        )

        if profit_rate <= cfg.STOP_LOSS_RATE:
            logger.warning(f"[손절] {name} 수익률 {profit_rate:.1f}% - 즉시 매도")
            success = broker.sell_order(ticker=ticker, qty=qty)
            if success:
                notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                notifier.send(f"[손절 매도] {name} | 손실률 {profit_rate:.1f}%")
            trace.record(
                "sell_stop_loss_result",
                stock_name=name,
                ticker=ticker,
                qty=qty,
                profit_rate=profit_rate,
                success=success,
            )
            continue

        if profit_rate >= cfg.TAKE_PROFIT_RATE:
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
                logger.info(f"[{decision.ai_model}] {name} → {decision.action}: {decision.reason}")
                decisions.append(decision)
                trace.record(
                    "sell_ai_decision",
                    stock_name=name,
                    ticker=ticker,
                    ai_model=decision.ai_model,
                    action=decision.action,
                    reason=decision.reason,
                    profit_rate=profit_rate,
                )

            final_decision = decision_maker.decide_sell_by_vote(decisions)
            logger.info(f"최종 결정: {name} → {final_decision.action}")
            trace.record(
                "sell_final_decision",
                stock_name=name,
                ticker=ticker,
                action=final_decision.action,
                reason=final_decision.reason,
            )

            if final_decision.action == "매도":
                success = broker.sell_order(ticker=ticker, qty=qty)
                if success:
                    notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                    notifier.send(f"[매도 이유] {final_decision.reason}")
                trace.record(
                    "sell_order_result",
                    stock_name=name,
                    ticker=ticker,
                    qty=qty,
                    success=success,
                    reason=final_decision.reason,
                )
        else:
            logger.info(f"{name}: 매도 조건 미달 ({profit_rate:.1f}%) - 보유 유지")
            trace.record(
                "sell_hold",
                stock_name=name,
                ticker=ticker,
                profit_rate=profit_rate,
                take_profit_rate=cfg.TAKE_PROFIT_RATE,
            )

    logger.info("[매도 로직] 완료")


def run_status(broker, notifier: TelegramNotifier, trace: TraceRecorder):
    """현재 계좌 현황을 텔레그램으로 전송합니다."""
    logger.info("[현황 보고] 시작")
    balance = broker.get_balance()
    holdings = broker.get_holdings()
    notifier.notify_daily_summary(balance=balance, holdings=holdings)
    trace.record("status_report", balance=balance, holdings_count=len(holdings))
    logger.info("[현황 보고] 완료")


def execute_mode(mode: BotMode, run_id: str | None = None) -> Dict[str, Any]:
    """
    buy/sell/status 중 하나를 실행하고 결과를 반환합니다.
    """
    cfg = _prepare_runtime()
    trading_mode = "실전투자" if cfg.IS_REAL_TRADING else "모의투자"
    effective_run_id = run_id or uuid.uuid4().hex[:12]
    trace = TraceRecorder(mode=mode, run_id=effective_run_id, trading_mode=trading_mode)

    trace.record("run_start")
    logger.info(f"봇 시작 | 모드: {mode} | 환경: {trading_mode}")

    try:
        broker = cfg.get_broker()
        analyzers = cfg.get_analyzers()
        notifier = TelegramNotifier()
        validator = StockValidator()

        if mode == "buy":
            run_buy_logic(broker, analyzers, validator, notifier, cfg, trace)
        elif mode == "sell":
            run_sell_logic(broker, analyzers, notifier, cfg, trace)
        else:
            run_status(broker, notifier, trace)

        trace.record("run_end", status="success")
        return {"ok": True, "returncode": 0, "output": f"{mode} 실행 완료", "run_id": effective_run_id}

    except Exception as e:
        logger.error(f"치명적 오류 발생: {e}", exc_info=True)
        trace.record("run_end", status="failed", error=str(e))
        try:
            TelegramNotifier().notify_error("execute_mode()", e)
        except Exception:
            pass
        return {"ok": False, "returncode": 1, "output": str(e), "run_id": effective_run_id}
