"""보유 종목 모니터링 데몬.

사용자별 보유 종목의 가격 변동을 주기적으로 관찰하고,
설정된 기준을 초과하면 AI 매도/보유 판단을 트리거합니다.
"""
import logging
import threading
import time
import uuid
from typing import Any

import config as config_module
import db as db_module
from bot_service import TraceRecorder, run_sell_logic
from user_context import UserContext

logger = logging.getLogger(__name__)

# 모니터 상태 (전역)
MONITOR_STATE_LOCK = threading.Lock()
MONITOR_STATE: dict[str, Any] = {
    "running": False,
    "last_check": "",
    "active_users": 0,
    "alerts": [],  # 최근 알림 리스트 (최대 50개)
}

_MONITOR_STARTED = False


def get_monitor_state() -> dict:
    with MONITOR_STATE_LOCK:
        return dict(MONITOR_STATE)


def _check_user_holdings(user_id: int, monitor_cfg: dict) -> list[dict]:
    """사용자의 보유 종목을 점검하고 기준 초과 종목을 반환합니다."""
    alerts = []
    try:
        ctx = UserContext.from_user_id(user_id)
        broker = ctx.get_broker()
        holdings = broker.get_holdings()

        if not holdings:
            return []

        profit_threshold = monitor_cfg.get("profit_threshold", 5.0)
        loss_threshold = monitor_cfg.get("loss_threshold", -3.0)
        volatility_threshold = monitor_cfg.get("volatility_threshold", 3.0)

        for h in holdings:
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            profit_rate = h.get("profit_rate", 0.0)
            current_price = h.get("current_price", 0)
            avg_price = h.get("avg_price", 0)
            qty = h.get("qty", 0)

            # 쿨다운 중인 종목은 알림/AI 판단 스킵
            try:
                if db_module.is_in_sell_cooldown(user_id, ticker):
                    logger.debug(f"[모니터] {name} ({ticker}) 쿨다운 중 — 스킵")
                    continue
            except Exception:
                pass

            alert = None

            # 수익 기준 초과
            if profit_rate >= profit_threshold:
                alert = {
                    "type": "profit_threshold",
                    "stock_name": name,
                    "ticker": ticker,
                    "profit_rate": profit_rate,
                    "threshold": profit_threshold,
                    "message": f"[수익 알림] {name} ({ticker}) 수익률 {profit_rate:+.1f}% (기준: {profit_threshold:+.1f}%)",
                    "qty": qty,
                    "current_price": current_price,
                    "avg_price": avg_price,
                }

            # 손실 기준 초과
            elif profit_rate <= loss_threshold:
                alert = {
                    "type": "loss_threshold",
                    "stock_name": name,
                    "ticker": ticker,
                    "profit_rate": profit_rate,
                    "threshold": loss_threshold,
                    "message": f"[손실 알림] {name} ({ticker}) 수익률 {profit_rate:+.1f}% (기준: {loss_threshold:+.1f}%)",
                    "qty": qty,
                    "current_price": current_price,
                    "avg_price": avg_price,
                }

            if alert:
                alert["user_id"] = user_id
                alert["checked_at"] = config_module.now().strftime("%Y-%m-%d %H:%M:%S")
                alerts.append(alert)

    except Exception as e:
        logger.warning(f"모니터링 점검 실패 (user_id={user_id}): {e}")

    return alerts


def _trigger_ai_sell_check(user_id: int, monitor_cfg: dict) -> None:
    """AI 매도 판단을 트리거합니다."""
    if not monitor_cfg.get("auto_sell_enabled", False):
        return

    try:
        from bot_service import execute_mode
        run_id = uuid.uuid4().hex[:12]
        logger.info(f"[모니터] AI 매도 판단 트리거 (user_id={user_id}, run_id={run_id})")
        result = execute_mode("sell", run_id=run_id, user_id=user_id)
        logger.info(f"[모니터] AI 매도 판단 완료: {result.get('ok')}")
    except Exception as e:
        logger.error(f"[모니터] AI 매도 판단 오류 (user_id={user_id}): {e}")


def _send_monitor_notification(user_id: int, alerts: list[dict]) -> None:
    """모니터링 알림을 텔레그램으로 전송합니다."""
    try:
        ctx = UserContext.from_user_id(user_id)
        notifier = ctx.get_notifier()
        for alert in alerts:
            notifier.send(alert["message"])
    except Exception as e:
        logger.warning(f"모니터링 알림 전송 실패 (user_id={user_id}): {e}")


def _monitor_loop() -> None:
    """모니터링 메인 루프. 모든 활성 사용자의 보유 종목을 주기적으로 점검합니다."""
    # 사용자별 마지막 점검 시간 추적
    last_checked: dict[int, float] = {}

    while True:
        try:
            monitors = db_module.get_all_active_monitors()
            now = time.time()

            with MONITOR_STATE_LOCK:
                MONITOR_STATE["running"] = True
                MONITOR_STATE["active_users"] = len(monitors)
                MONITOR_STATE["last_check"] = config_module.now().strftime("%Y-%m-%d %H:%M:%S")

            # 만료된 쿨다운 레코드 정리 (루프마다)
            try:
                db_module.clear_expired_cooldowns()
            except Exception:
                pass

            for monitor_cfg in monitors:
                user_id = monitor_cfg["user_id"]
                interval = monitor_cfg.get("check_interval_sec", 300)

                # 점검 주기 확인
                if now - last_checked.get(user_id, 0) < interval:
                    continue
                last_checked[user_id] = now

                # 보유 종목 점검
                alerts = _check_user_holdings(user_id, monitor_cfg)

                if alerts:
                    # 알림 전송
                    if monitor_cfg.get("notify_on_threshold", True):
                        _send_monitor_notification(user_id, alerts)

                    # 전역 상태에 알림 기록
                    with MONITOR_STATE_LOCK:
                        MONITOR_STATE["alerts"] = (alerts + MONITOR_STATE["alerts"])[:50]

                    # AI 매도 판단 트리거
                    has_loss = any(a["type"] == "loss_threshold" for a in alerts)
                    has_profit = any(a["type"] == "profit_threshold" for a in alerts)
                    if has_loss or has_profit:
                        # AI 판단은 별도 스레드에서 실행 (블로킹 방지)
                        thread = threading.Thread(
                            target=_trigger_ai_sell_check,
                            args=(user_id, monitor_cfg),
                            daemon=True,
                        )
                        thread.start()

        except Exception as e:
            logger.error(f"[모니터 루프] 오류: {e}")
            with MONITOR_STATE_LOCK:
                MONITOR_STATE["last_check"] = f"오류: {e}"

        time.sleep(10)  # 기본 10초마다 루프 (사용자별 interval은 별도)


def ensure_monitor_started() -> None:
    """모니터링 데몬을 시작합니다 (1회만)."""
    global _MONITOR_STARTED
    if _MONITOR_STARTED:
        return
    _MONITOR_STARTED = True
    thread = threading.Thread(target=_monitor_loop, daemon=True, name="monitor-daemon")
    thread.start()
    logger.info("[모니터] 데몬 시작됨")
