"""
매매 백엔드 공용 실행 서비스.

CLI(main.py)와 웹(web_admin.py)에서 동일한 실행 경로를 사용합니다.
"""
import importlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Literal

import config as config_module
from notifiers import TelegramNotifier
from utils import DecisionMaker, HoldingsTracker, MarketDataProvider, StockValidator

BotMode = Literal["buy", "sell", "status", "ask"]
TRACE_PATH = Path("data/ai_traces.jsonl")

logger = logging.getLogger(__name__)


class TraceRecorder:
    """AI 판단 과정을 구조화해 저장합니다."""

    def __init__(self, mode: BotMode, run_id: str, trading_mode: str, user_id: int = 0):
        self.mode = mode
        self.run_id = run_id
        self.trading_mode = trading_mode
        self.user_id = user_id

    def record(self, event_type: str, **payload: Any) -> None:
        now_str = config_module.now().strftime("%Y-%m-%d %H:%M:%S")

        # DB 모드 (user_id > 0)
        if self.user_id > 0:
            try:
                import db as db_module
                db_module.insert_trace(
                    user_id=self.user_id,
                    run_id=self.run_id,
                    mode=self.mode,
                    trading_mode=self.trading_mode,
                    event_type=event_type,
                    payload=payload,
                    created_at=now_str,
                )
                return
            except Exception:
                pass  # DB 실패 시 파일 폴백

        # 레거시: JSONL 파일
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": now_str,
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
        # 프롬프트 추적: build_buy_prompt로 실제 전송 프롬프트 복원
        try:
            from utils.prompt_manager import build_buy_prompt
            sent_prompt = build_buy_prompt(balance=balance)
        except Exception:
            sent_prompt = ""
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
                prompt=sent_prompt,
            )
        else:
            trace.record(
                "buy_recommendation_empty",
                analyzer=analyzer_name,
                reason=getattr(analyzer, "last_recommendation_error", "") or "추천 없음",
                prompt=sent_prompt,
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

    total_budget = int(balance * cfg.BUY_BUDGET_RATIO)
    remaining_budget = total_budget
    remaining_stocks = len(agreed_stock_names)
    trace.record(
        "buy_budget_plan",
        total_budget=total_budget,
        stock_count=remaining_stocks,
        budget_per_stock=total_budget // max(1, remaining_stocks),
    )

    # 종목 검증 및 현재가 조회 (가격 오름차순 정렬용)
    buy_candidates = []
    for agreed_stock_name in agreed_stock_names:
        ticker = validator.verify_and_get_code(agreed_stock_name)
        if not ticker:
            msg = f"'{agreed_stock_name}'은 KRX 미상장 종목 또는 오류입니다. (AI 환각 차단)"
            logger.warning(msg)
            notifier.send(f"[매수 차단] {msg}")
            trace.record("buy_blocked", stock_name=agreed_stock_name, reason=msg)
            remaining_stocks -= 1
            continue

        current_price = broker.get_current_price(ticker)
        if not current_price or current_price <= 0:
            msg = f"'{agreed_stock_name}' 현재가 조회 실패. 매수를 중단합니다."
            logger.error(msg)
            notifier.send(f"[매수 오류] {msg}")
            trace.record("buy_error", stock_name=agreed_stock_name, ticker=ticker, reason=msg)
            remaining_stocks -= 1
            continue

        buy_candidates.append({"name": agreed_stock_name, "ticker": ticker, "price": current_price})

    # 싼 종목부터 매수 시도 (예산 재분배 효과 극대화)
    buy_candidates.sort(key=lambda c: c["price"])
    remaining_stocks = len(buy_candidates)

    bought_count = 0
    for candidate in buy_candidates:
        agreed_stock_name = candidate["name"]
        ticker = candidate["ticker"]
        current_price = candidate["price"]

        # 남은 예산을 남은 종목 수로 재분배
        budget_per_stock = remaining_budget // max(1, remaining_stocks)

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
            remaining_stocks -= 1
            continue

        ai_list = [r.ai_model for r in recommendations if r.stock_name == agreed_stock_name]
        reasons = [r.reason for r in recommendations if r.stock_name == agreed_stock_name]
        logger.info(f"매수 주문: {agreed_stock_name} ({ticker}) {qty}주 @ {current_price:,}원")

        success = broker.buy_order(ticker=ticker, qty=qty)
        spent = qty * current_price
        broker_error = getattr(broker, "last_order_error", "")
        if success:
            bought_count += 1
            remaining_budget -= spent
            HoldingsTracker(user_id=trace.user_id).record_buy(ticker)
            notifier.notify_buy_order(agreed_stock_name, ticker, qty, current_price)
            detail_msg = (
                f"\n추천 AI: {', '.join(ai_list)}\n"
                f"이유: {' / '.join(reasons)}"
            )
            notifier.send(detail_msg)
        else:
            err_lower = broker_error.lower()
            # 잔고 부족 계열 에러이면 루프 전체 종료 (반복 매수 시도 방지)
            is_balance_error = any(kw in broker_error for kw in [
                "잔고", "부족", "초과", "주문가능", "매수가능", "잔액",
                "BSID0013", "잔고부족",  # KIS 에러 코드
            ]) or any(kw in err_lower for kw in ["insuffic", "balance", "fund"])
            notifier.send(
                f"[매수 실패] {agreed_stock_name} ({ticker}) {qty}주 주문 실패"
                + (f"\n사유: {broker_error}" if broker_error else "")
            )

        # trade_log 기록
        try:
            import db as _db
            _db.insert_trade_log(
                user_id=trace.user_id,
                action="buy",
                ticker=ticker,
                stock_name=agreed_stock_name,
                qty=qty,
                price=current_price,
                reason=" / ".join(reasons),
                ai_decisions={"ai_models": ai_list, "reasons": reasons},
                status="success" if success else "failed",
                run_id=trace.run_id,
            )
        except Exception:
            pass

        remaining_stocks -= 1
        trace.record(
            "buy_order_result",
            success=success,
            stock_name=agreed_stock_name,
            ticker=ticker,
            qty=qty,
            current_price=current_price,
            ai_models=ai_list,
            reasons=reasons,
            broker_error=broker_error,
        )

        # 잔고 부족 에러이면 더 이상 다른 종목도 살 수 없으므로 루프 중단
        if not success and is_balance_error:
            msg = (
                f"[매수 중단] 실제 사용 가능한 잔고가 없습니다. "
                f"API 잔고({balance:,}원)는 D+2 미결제 자금을 포함한 값일 수 있습니다. "
                f"나머지 {remaining_stocks}개 종목 매수를 건너뜁니다."
            )
            logger.warning(msg)
            notifier.send(msg)
            trace.record("buy_aborted_no_balance", reason=msg, remaining=remaining_stocks)
            break

    trace.record(
        "buy_summary",
        target_count=len(agreed_stock_names),
        bought_count=bought_count,
    )

    logger.info("[매수 로직] 완료")


def run_sell_logic(broker, analyzers, notifier: TelegramNotifier, cfg, trace: TraceRecorder):
    """매도 로직: 시장 급락 감지 → 동적 손절 → AI 판단 (사각지대 제거)"""
    logger.info("=" * 50)
    logger.info("[매도 로직] 시작")

    holdings = broker.get_holdings()
    trace.record("sell_holdings_loaded", count=len(holdings))
    if not holdings:
        logger.info("보유 종목 없음. 매도 로직을 건너뜁니다.")
        trace.record("sell_skipped", reason="보유 종목 없음")
        return

    # 헬퍼 초기화
    market_data = MarketDataProvider()
    tracker = HoldingsTracker(user_id=trace.user_id)
    tracker.sync_from_holdings(holdings)
    decision_maker = DecisionMaker()

    # ── 1단계: 시장 급락 감지 → 손실 종목만 방어 매도, 수익 종목은 AI 판단 ──
    crash_threshold = getattr(cfg, "MARKET_CRASH_THRESHOLD", -3.5)
    is_crash = market_data.is_market_crash(threshold=crash_threshold)
    crash_msg = ""
    if is_crash:
        idx = market_data.get_market_index_change()
        crash_msg = (
            f"KOSPI {idx['kospi_change_pct']:+.1f}% / KOSDAQ {idx['kosdaq_change_pct']:+.1f}%"
            if idx else "지수 데이터 없음"
        )
        logger.warning(f"[시장 급락] {crash_msg}")
        notifier.send(f"[시장 급락 감지] {crash_msg}")

        # 손실 중인 종목만 즉시 방어 매도
        loss_holdings = [h for h in holdings if h["profit_rate"] < 0]
        profit_holdings = [h for h in holdings if h["profit_rate"] >= 0]

        for holding in loss_holdings:
            logger.warning(f"[급락 방어 매도] {holding['name']} 수익률 {holding['profit_rate']:.1f}% — 손실 종목 즉시 매도")
            success = broker.sell_order(ticker=holding["ticker"], qty=holding["qty"])
            if success:
                notifier.notify_sell_order(
                    holding["name"], holding["ticker"], holding["qty"],
                    holding["avg_price"], holding["current_price"],
                )
                notifier.send(f"[급락 방어 매도] {holding['name']} | 수익률 {holding['profit_rate']:.1f}%")
                tracker.record_sell(holding["ticker"])
            # trade_log 기록
            try:
                import db as _db
                _db.insert_trade_log(
                    user_id=trace.user_id, action="sell",
                    ticker=holding["ticker"], stock_name=holding["name"],
                    qty=holding["qty"], price=holding["current_price"],
                    profit_rate=holding["profit_rate"],
                    profit_amount=(holding["current_price"] - holding["avg_price"]) * holding["qty"],
                    reason=f"시장 급락 방어 매도: {crash_msg}",
                    status="success" if success else "failed",
                    run_id=trace.run_id,
                )
            except Exception:
                pass
            trace.record(
                "sell_market_crash",
                stock_name=holding["name"],
                ticker=holding["ticker"],
                qty=holding["qty"],
                profit_rate=holding["profit_rate"],
                success=success,
                crash_info=crash_msg,
            )

        if not profit_holdings:
            logger.info("[매도 로직] 시장 급락 방어 매도 완료 (수익 종목 없음)")
            return

        # 수익 중인 종목은 아래 2단계에서 AI가 판단 (crash_msg를 context에 포함)
        logger.info(f"수익 중 {len(profit_holdings)}개 종목은 AI 판단으로 전환")
        holdings = profit_holdings

    # ── 2단계: 종목별 분석 ──
    atr_multiplier = getattr(cfg, "TRAILING_STOP_ATR_MULTIPLIER", 2.0)

    # 쿨다운 설정 로드 (사용자 설정 → 기본값 180분 = 3시간)
    try:
        import db as _db
        _sell_cooldown_min = int(
            _db.get_user_config(trace.user_id).get("SELL_COOLDOWN_MINUTES", "") or 180
        )
    except Exception:
        _sell_cooldown_min = 180

    for holding in holdings:
        name = holding["name"]
        ticker = holding["ticker"]
        qty = holding["qty"]
        avg_price = holding["avg_price"]
        current_price = holding["current_price"]
        profit_rate = holding["profit_rate"]

        # 쿨다운 체크: 이전에 "보유" 결정된 종목은 쿨다운 시간 동안 AI 판단 스킵
        try:
            import db as _db
            cooldown_info = _db.get_sell_cooldown_info(trace.user_id, ticker)
            if cooldown_info:
                logger.info(
                    f"[쿨다운] {name} ({ticker}) → "
                    f"{cooldown_info['cooldown_until']}까지 AI 판단 스킵"
                )
                trace.record(
                    "sell_cooldown_skip",
                    stock_name=name, ticker=ticker,
                    cooldown_until=cooldown_info["cooldown_until"],
                    profit_rate=profit_rate,
                )
                continue
        except Exception:
            pass

        logger.info(
            f"보유 종목 검토: {name} ({ticker}) "
            f"{qty}주 | 수익률 {profit_rate:.1f}%"
        )

        # 보유 기간 및 트레일링 하이 갱신
        holding_days = tracker.get_holding_days(ticker)
        tracker.update_trailing_high(ticker, current_price)
        trailing_high = tracker.get_trailing_high(ticker)

        # enriched context 생성 (기술 지표 + 시장 현황)
        enriched_context = market_data.build_enriched_context(
            ticker=ticker,
            holding_days=holding_days,
            trailing_high=trailing_high,
            atr_multiplier=atr_multiplier,
        )
        if is_crash and crash_msg:
            enriched_context = f"[경고: 시장 급락 중] {crash_msg}\n{enriched_context}"

        trace.record(
            "sell_holding_checked",
            stock_name=name,
            ticker=ticker,
            qty=qty,
            avg_price=avg_price,
            current_price=current_price,
            profit_rate=profit_rate,
            holding_days=holding_days,
            trailing_high=trailing_high,
            enriched_context=enriched_context,
        )

        # ── 2a: 동적 손절 (ATR 트레일링 스탑) ──
        df = market_data.get_daily_prices(ticker)
        atr = market_data.compute_atr(df) if df is not None else None

        if atr and trailing_high:
            dynamic_stop = trailing_high - (atr * atr_multiplier)
            if current_price <= dynamic_stop:
                logger.warning(
                    f"[동적 손절] {name} 현재가 {current_price:,} ≤ "
                    f"트레일링 스탑 {dynamic_stop:,.0f} (고가 {trailing_high:,} - ATR {atr:,.0f}×{atr_multiplier})"
                )
                success = broker.sell_order(ticker=ticker, qty=qty)
                if success:
                    notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                    notifier.send(
                        f"[동적 손절] {name} | ATR 트레일링 스탑 발동\n"
                        f"고가 {trailing_high:,} → 손절라인 {dynamic_stop:,.0f}원"
                    )
                    tracker.record_sell(ticker)
                try:
                    import db as _db
                    _db.insert_trade_log(
                        user_id=trace.user_id, action="sell",
                        ticker=ticker, stock_name=name, qty=qty, price=current_price,
                        profit_rate=profit_rate,
                        profit_amount=(current_price - avg_price) * qty,
                        reason=f"ATR 트레일링 스탑 (고가 {trailing_high:,} - ATR {atr:,.0f}x{atr_multiplier})",
                        status="success" if success else "failed",
                        run_id=trace.run_id,
                    )
                except Exception:
                    pass
                trace.record(
                    "sell_trailing_stop",
                    stock_name=name, ticker=ticker, qty=qty,
                    profit_rate=profit_rate, success=success,
                    trailing_high=trailing_high, atr=atr,
                    dynamic_stop=round(dynamic_stop),
                )
                continue

        # ── 2b: 고정 손절 (ATR 데이터 없을 때 폴백) ──
        if profit_rate <= cfg.STOP_LOSS_RATE:
            logger.warning(f"[고정 손절] {name} 수익률 {profit_rate:.1f}% - 즉시 매도")
            success = broker.sell_order(ticker=ticker, qty=qty)
            if success:
                notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                notifier.send(f"[손절 매도] {name} | 손실률 {profit_rate:.1f}%")
                tracker.record_sell(ticker)
            try:
                import db as _db
                _db.insert_trade_log(
                    user_id=trace.user_id, action="sell",
                    ticker=ticker, stock_name=name, qty=qty, price=current_price,
                    profit_rate=profit_rate,
                    profit_amount=(current_price - avg_price) * qty,
                    reason=f"고정 손절 (기준: {cfg.STOP_LOSS_RATE}%)",
                    status="success" if success else "failed",
                    run_id=trace.run_id,
                )
            except Exception:
                pass
            trace.record(
                "sell_stop_loss",
                stock_name=name, ticker=ticker, qty=qty,
                profit_rate=profit_rate, success=success,
            )
            continue

        # ── 2c: AI 투표 (모든 나머지 경우 — 사각지대 제거) ──
        # 프롬프트 추적
        try:
            from utils.prompt_manager import build_sell_prompt
            sent_sell_prompt = build_sell_prompt(
                stock_name=name, ticker=ticker, qty=qty,
                avg_price=avg_price, current_price=current_price,
                profit_rate=profit_rate, market_info=enriched_context,
            )
        except Exception:
            sent_sell_prompt = ""
        decisions = []
        for analyzer in analyzers:
            decision = analyzer.decide_sell(
                stock_name=name,
                ticker=ticker,
                qty=qty,
                avg_price=avg_price,
                current_price=current_price,
                profit_rate=profit_rate,
                market_info=enriched_context,
            )
            logger.info(f"[{decision.ai_model}] {name} → {decision.action}: {decision.reason}")
            decisions.append(decision)
            trace.record(
                "sell_ai_decision",
                stock_name=name, ticker=ticker,
                ai_model=decision.ai_model,
                action=decision.action,
                reason=decision.reason,
                profit_rate=profit_rate,
                prompt=sent_sell_prompt,
            )

        final_decision = decision_maker.decide_sell_by_vote(decisions)
        logger.info(f"최종 결정: {name} → {final_decision.action}")

        # 개별 AI 판단 요약 (텔레그램 + trace 공용)
        valid_decisions = [d for d in decisions if not d.is_error]
        error_decisions = [d for d in decisions if d.is_error]
        vote_lines = []
        for d in valid_decisions:
            vote_lines.append(f"  {d.ai_model}: {d.action} - {d.reason[:60]}")
        for d in error_decisions:
            vote_lines.append(f"  {d.ai_model}: ⚠️ 오류 (투표 제외)")
        vote_summary = "\n".join(vote_lines)

        trace.record(
            "sell_final_decision",
            stock_name=name, ticker=ticker,
            action=final_decision.action,
            reason=final_decision.reason,
            profit_rate=profit_rate,
            holding_days=holding_days,
            valid_votes=len(valid_decisions),
            error_votes=len(error_decisions),
        )

        if final_decision.action == "매도":
            # 매도 실행 시 쿨다운 해제
            try:
                import db as _db
                _db.clear_sell_cooldown(trace.user_id, ticker)
            except Exception:
                pass

            success = broker.sell_order(ticker=ticker, qty=qty)
            if success:
                notifier.notify_sell_order(name, ticker, qty, avg_price, current_price)
                days_text = f" | {holding_days}일 보유" if holding_days else ""
                notifier.send(
                    f"[AI 판단: 매도] {name} ({ticker})\n"
                    f"수익률 {profit_rate:+.1f}%{days_text}\n"
                    f"{vote_summary}\n"
                    f"사유: {final_decision.reason}"
                )
                tracker.record_sell(ticker)
            # trade_log 기록 (AI 판단 내역 포함)
            try:
                import db as _db
                ai_detail = {
                    "decisions": [
                        {"ai_model": d.ai_model, "action": d.action, "reason": d.reason}
                        for d in valid_decisions
                    ],
                    "errors": [d.ai_model for d in error_decisions],
                    "final": final_decision.action,
                }
                _db.insert_trade_log(
                    user_id=trace.user_id, action="sell",
                    ticker=ticker, stock_name=name, qty=qty, price=current_price,
                    profit_rate=profit_rate,
                    profit_amount=(current_price - avg_price) * qty,
                    reason=final_decision.reason,
                    ai_decisions=ai_detail,
                    status="success" if success else "failed",
                    run_id=trace.run_id,
                )
            except Exception:
                pass
            trace.record(
                "sell_order_result",
                stock_name=name, ticker=ticker, qty=qty,
                success=success, reason=final_decision.reason,
            )
        else:
            # 보유 결정 → 쿨다운 설정 (다음 _sell_cooldown_min 동안 재판단 방지)
            try:
                import db as _db
                _db.set_sell_cooldown(
                    trace.user_id, ticker, _sell_cooldown_min, decided_action="hold",
                )
                logger.info(
                    f"[쿨다운 설정] {name} ({ticker}) → {_sell_cooldown_min}분간 AI 재판단 방지"
                )
            except Exception:
                pass

            days_text = f" | {holding_days}일 보유" if holding_days else ""
            notifier.send(
                f"[AI 판단: 보유] {name} ({ticker})\n"
                f"수익률 {profit_rate:+.1f}%{days_text}\n"
                f"(다음 판단: {_sell_cooldown_min}분 후)\n"
                f"{vote_summary}"
            )
            trace.record(
                "sell_hold",
                stock_name=name, ticker=ticker,
                profit_rate=profit_rate,
                reason=final_decision.reason,
                cooldown_minutes=_sell_cooldown_min,
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


_STOCK_QUERY_PATTERNS = [
    r"(.+?)(?:\s*주식)?\s*(?:이|가)\s*궁금",
    r"(.+?)\s*주식\s*(?:알려|분석|조회|검색)",
    r"(.+?)\s*(?:알려|분석|조회|검색)",
    r"(.+?)\s*(?:어때|어떤가|어떨까|전망|분석해)",
    r"(.+?)\s*주식",
]


def parse_stock_name(query: str) -> str:
    """자연어 질문에서 종목명을 추출합니다."""
    query = query.strip()
    for pattern in _STOCK_QUERY_PATTERNS:
        m = re.search(pattern, query)
        if m:
            name = m.group(1).strip()
            if name:
                return name
    return query


def run_ask_logic(
    query: str,
    broker,
    analyzers,
    validator: StockValidator,
    trace: TraceRecorder,
) -> str:
    """종목 질문 로직: 종목명 파싱 → 검증 → 현재가 → AI 분석 → 결과 출력"""
    logger.info("=" * 50)
    logger.info(f"[종목 조회] 질문: {query}")

    stock_name = parse_stock_name(query)
    logger.info(f"[종목 조회] 추출된 종목명: {stock_name}")
    trace.record("ask_parsed", query=query, stock_name=stock_name)

    ticker = validator.verify_and_get_code(stock_name)
    if not ticker:
        msg = f"'{stock_name}'은(는) KRX에 상장된 종목이 아닙니다."
        logger.warning(msg)
        trace.record("ask_not_found", stock_name=stock_name)
        return msg

    current_price = broker.get_current_price(ticker)
    if not current_price or current_price <= 0:
        msg = f"'{stock_name}' ({ticker}) 현재가를 조회할 수 없습니다."
        logger.error(msg)
        trace.record("ask_price_error", stock_name=stock_name, ticker=ticker)
        return msg

    logger.info(f"[종목 조회] {stock_name} ({ticker}) 현재가: {current_price:,}원")

    analyses = []
    for analyzer in analyzers:
        analysis = analyzer.analyze_stock(
            stock_name=stock_name,
            ticker=ticker,
            current_price=current_price,
        )
        if analysis:
            analyses.append(analysis)
            trace.record(
                "ask_analysis",
                ai_model=analysis.ai_model,
                opinion=analysis.opinion,
                one_liner=analysis.one_liner,
            )

    if not analyses:
        msg = f"{stock_name} ({ticker}) | 현재가: {current_price:,}원\nAI 분석을 가져올 수 없습니다."
        logger.warning(msg)
        return msg

    lines = [
        f"{'=' * 50}",
        f"  {stock_name} ({ticker}) | 현재가: {current_price:,}원",
        f"{'=' * 50}",
    ]

    for a in analyses:
        lines.append(f"\n[{a.ai_model}]")
        lines.append(f"  기업 개요: {a.summary}")
        lines.append(f"  최근 이슈: {a.recent_issues}")
        lines.append(f"  강점: {a.strengths}")
        lines.append(f"  리스크: {a.risks}")
        lines.append(f"  종합 의견: {a.opinion}")
        lines.append(f"  한줄 요약: {a.one_liner}")

    if len(analyses) > 1:
        opinions = [a.opinion for a in analyses]
        lines.append(f"\n{'─' * 50}")
        lines.append(f"  AI 의견 종합: {' / '.join(opinions)}")

    lines.append(f"{'=' * 50}")

    result = "\n".join(lines)
    logger.info(f"[종목 조회] 완료\n{result}")
    return result


def execute_mode(mode: BotMode, run_id: str | None = None, query: str = "",
                  user_id: int = 0) -> Dict[str, Any]:
    """
    buy/sell/status/ask 중 하나를 실행하고 결과를 반환합니다.
    user_id > 0이면 DB에서 사용자 설정을 로드, 아니면 .env 폴백.
    """
    cfg = _prepare_runtime()
    effective_run_id = run_id or uuid.uuid4().hex[:12]

    # UserContext로 사용자별 의존성 생성
    if user_id > 0:
        from user_context import UserContext
        ctx = UserContext.from_user_id(user_id)
    else:
        from user_context import UserContext
        ctx = UserContext.from_env_fallback()

    is_real = ctx.config.get("IS_REAL_TRADING", "False").lower() == "true"
    trading_mode = "실전투자" if is_real else "모의투자"
    trace = TraceRecorder(mode=mode, run_id=effective_run_id,
                          trading_mode=trading_mode, user_id=ctx.user_id)

    trace.record("run_start")
    logger.info(f"봇 시작 | 모드: {mode} | 환경: {trading_mode} | 사용자: {ctx.username}")

    try:
        broker = ctx.get_broker()
        analyzers = ctx.get_analyzers()
        notifier = ctx.get_notifier()
        validator = StockValidator()

        if mode == "buy":
            run_buy_logic(broker, analyzers, validator, notifier, cfg, trace)
        elif mode == "sell":
            run_sell_logic(broker, analyzers, notifier, cfg, trace)
        elif mode == "ask":
            result_text = run_ask_logic(query, broker, analyzers, validator, trace)
            trace.record("run_end", status="success")
            return {"ok": True, "returncode": 0, "output": result_text, "run_id": effective_run_id}
        else:
            run_status(broker, notifier, trace)

        trace.record("run_end", status="success")
        return {"ok": True, "returncode": 0, "output": f"{mode} 실행 완료", "run_id": effective_run_id}

    except Exception as e:
        logger.error(f"치명적 오류 발생: {e}", exc_info=True)
        trace.record("run_end", status="failed", error=str(e))
        try:
            ctx.get_notifier().notify_error("execute_mode()", e)
        except Exception:
            pass
        return {"ok": False, "returncode": 1, "output": str(e), "run_id": effective_run_id}
