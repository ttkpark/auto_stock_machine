"""봇 인스턴스 관리자.

모든 봇(매수, 매도, 모니터, 수동매매)을 동일한 인스턴스 모델로 관리합니다.
각 봇은 이름, 타입, 활성 상태, 개별 설정(config)을 가집니다.
"""
import json
import logging
import uuid
from typing import Any

import config as config_module
import db as db_module
from bot_service import TraceRecorder, execute_mode
from user_context import UserContext

logger = logging.getLogger(__name__)

# 봇 타입 정의
BOT_TYPES = {
    "buy_auto": {"label": "자동 매수", "description": "AI 추천 기반 자동 매수"},
    "sell_auto": {"label": "자동 매도", "description": "AI 판단 기반 자동 매도"},
    "monitor": {"label": "모니터링", "description": "보유 종목 변동 감시 및 알림"},
    "manual": {"label": "수동 매매", "description": "사용자 수동 매수/매도 실행"},
}

# 봇 설정 기본값 (bot.config에 저장)
DEFAULT_BOT_CONFIG = {
    "buy_auto": {
        "budget_ratio": 0.9,
        "min_consensus": 1,
        "max_stocks": 3,
    },
    "sell_auto": {
        "stop_loss_rate": -3.0,
        "take_profit_rate": 5.0,
        "atr_multiplier": 2.0,
        "crash_threshold": -3.5,
    },
    "monitor": {
        "check_interval_sec": 300,
        "profit_alert": 5.0,
        "loss_alert": -3.0,
        "volatility_alert": 3.0,
    },
    "manual": {},
}


def get_bot_type_info() -> dict:
    """사용 가능한 봇 타입과 기본 설정 정보를 반환합니다."""
    return {
        "types": BOT_TYPES,
        "default_configs": DEFAULT_BOT_CONFIG,
    }


def create_bot_for_user(user_id: int, name: str, bot_type: str,
                        config: dict | None = None) -> dict:
    """사용자에게 새 봇을 생성합니다."""
    if bot_type not in BOT_TYPES:
        raise ValueError(f"지원하지 않는 봇 타입: {bot_type}")

    # 기본 설정에 사용자 설정 병합
    merged_config = dict(DEFAULT_BOT_CONFIG.get(bot_type, {}))
    if config:
        merged_config.update(config)

    bot_id = db_module.create_bot(
        user_id=user_id,
        name=name,
        bot_type=bot_type,
        enabled=True,
        config=merged_config,
    )
    return db_module.get_bot(bot_id)


def update_bot_config(bot_id: int, user_id: int, **fields) -> dict | None:
    """봇 설정을 업데이트합니다. 소유자 확인 포함."""
    bot = db_module.get_bot(bot_id)
    if not bot or bot["user_id"] != user_id:
        return None
    db_module.update_bot(bot_id, **fields)
    return db_module.get_bot(bot_id)


def delete_bot_for_user(bot_id: int, user_id: int) -> bool:
    """사용자의 봇을 삭제합니다."""
    bot = db_module.get_bot(bot_id)
    if not bot or bot["user_id"] != user_id:
        return False
    db_module.delete_bot(bot_id)
    return True


def execute_bot(bot_id: int, user_id: int, query: str = "") -> dict[str, Any]:
    """봇을 실행합니다. 봇 타입에 따라 적절한 모드를 실행합니다."""
    bot = db_module.get_bot(bot_id)
    if not bot or bot["user_id"] != user_id:
        return {"ok": False, "error": "봇을 찾을 수 없습니다."}
    if not bot["enabled"]:
        return {"ok": False, "error": "비활성화된 봇입니다."}

    bot_type = bot["bot_type"]
    bot_config = bot.get("config", {})

    # 봇 타입 → 실행 모드 매핑
    type_to_mode = {
        "buy_auto": "buy",
        "sell_auto": "sell",
        "manual": "manual",
        "monitor": "status",
    }
    mode = type_to_mode.get(bot_type, "status")

    run_id = uuid.uuid4().hex[:12]
    result = execute_mode(mode, run_id=run_id, user_id=user_id, query=query)

    # 체결 기록
    _record_bot_execution(bot, result, run_id)

    return {
        "ok": result.get("ok", False),
        "run_id": run_id,
        "bot_id": bot_id,
        "bot_name": bot["name"],
        "mode": mode,
        "output": result.get("output", ""),
    }


def _record_bot_execution(bot: dict, result: dict, run_id: str) -> None:
    """봇 실행 결과를 action_history에 기록합니다."""
    try:
        now_str = config_module.now().strftime("%Y-%m-%d %H:%M:%S")
        detail = f"[봇:{bot['name']}] [run_id={run_id}] {result.get('output', '')[:200]}"
        db_module.append_action(
            user_id=bot["user_id"],
            action=f"bot_{bot['bot_type']}",
            status="success" if result.get("ok") else "failed",
            detail=detail,
            created_at=now_str,
        )
    except Exception as e:
        logger.warning(f"봇 실행 기록 실패: {e}")


def execute_manual_trade(user_id: int, action: str, ticker: str,
                         qty: int, bot_id: int | None = None) -> dict[str, Any]:
    """수동 매매를 실행합니다."""
    if action not in ("buy", "sell"):
        return {"ok": False, "error": "action은 buy 또는 sell이어야 합니다."}
    if qty < 1:
        return {"ok": False, "error": "수량은 1 이상이어야 합니다."}

    try:
        ctx = UserContext.from_user_id(user_id)
        broker = ctx.get_broker()
        notifier = ctx.get_notifier()

        # 현재가 조회
        current_price = broker.get_current_price(ticker)
        if not current_price or current_price <= 0:
            return {"ok": False, "error": f"현재가 조회 실패: {ticker}"}

        if action == "buy":
            # 잔고 확인
            balance = broker.get_balance()
            required = current_price * qty
            if required > balance:
                return {
                    "ok": False,
                    "error": f"잔고 부족: 필요 {required:,}원, 보유 {balance:,}원",
                }
            success = broker.buy_order(ticker=ticker, qty=qty)
            if success:
                from utils import HoldingsTracker
                HoldingsTracker(user_id=user_id).record_buy(ticker)
                notifier.send(
                    f"[수동 매수] {ticker} {qty}주 @ {current_price:,}원\n"
                    f"총 {required:,}원"
                )
        else:
            success = broker.sell_order(ticker=ticker, qty=qty)
            if success:
                from utils import HoldingsTracker
                HoldingsTracker(user_id=user_id).record_sell(ticker)
                notifier.send(f"[수동 매도] {ticker} {qty}주 @ {current_price:,}원")

        # trade_log 기록
        db_module.insert_trade_log(
            user_id=user_id,
            action=action,
            ticker=ticker,
            stock_name="",
            qty=qty,
            price=current_price,
            reason="수동 매매",
            status="success" if success else "failed",
            bot_id=bot_id,
        )

        if success:
            return {
                "ok": True,
                "action": action,
                "ticker": ticker,
                "qty": qty,
                "price": current_price,
                "total": current_price * qty,
            }
        else:
            return {"ok": False, "error": f"{action} 주문 실패"}

    except Exception as e:
        logger.error(f"수동 매매 오류: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


def ensure_default_bots(user_id: int) -> None:
    """사용자에게 기본 봇이 없으면 자동 생성합니다."""
    existing = db_module.get_user_bots(user_id)
    if existing:
        return

    defaults = [
        ("기본 매수봇", "buy_auto"),
        ("기본 매도봇", "sell_auto"),
        ("모니터링봇", "monitor"),
        ("수동 매매봇", "manual"),
    ]
    for name, bot_type in defaults:
        create_bot_for_user(user_id, name, bot_type)
    logger.info(f"사용자 {user_id}에게 기본 봇 {len(defaults)}개 생성")
