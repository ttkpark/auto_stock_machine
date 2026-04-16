"""REST API Blueprint.

모든 앱(웹, 모바일)이 서버와 통신하는 JSON-only API 계층입니다.
기존 web_admin.py의 HTML 렌더링과 분리되어 동작합니다.

prefix: /api/v1
"""
import threading
import uuid
from functools import wraps

from flask import Blueprint, g, jsonify, request, session

import config as _cfg

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ------------------------------------------------------------------
# 인증 미들웨어
# ------------------------------------------------------------------

def _api_login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "인증이 필요합니다."}), 401
        g.user_id = session["user_id"]
        g.username = session.get("username", "")
        g.is_admin = session.get("is_admin", False)
        return func(*args, **kwargs)
    return wrapper


def _api_admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "인증이 필요합니다."}), 401
        g.user_id = session["user_id"]
        g.username = session.get("username", "")
        g.is_admin = session.get("is_admin", False)
        if not g.is_admin:
            return jsonify({"ok": False, "error": "관리자 권한이 필요합니다."}), 403
        return func(*args, **kwargs)
    return wrapper


# ==================================================================
# 인증 API
# ==================================================================

@api_bp.post("/auth/login")
def api_auth_login():
    """로그인."""
    import db as _db
    from auth import verify_password
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username or not password:
        return jsonify({"ok": False, "error": "사용자명과 비밀번호를 입력하세요."}), 400

    user = _db.get_user_by_username(username)
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        return jsonify({"ok": False, "error": "인증 실패."}), 401

    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["is_admin"] = bool(user["is_admin"])
    return jsonify({
        "ok": True,
        "user": {"id": user["id"], "username": user["username"],
                 "display_name": user["display_name"], "is_admin": bool(user["is_admin"])},
    })


@api_bp.post("/auth/logout")
def api_auth_logout():
    session.clear()
    return jsonify({"ok": True})


@api_bp.get("/me")
@_api_login_required
def api_me():
    """현재 사용자 정보."""
    return jsonify({
        "ok": True,
        "user": {"id": g.user_id, "username": g.username, "is_admin": g.is_admin},
    })


# ==================================================================
# 대시보드 / 계좌
# ==================================================================

@api_bp.get("/dashboard")
@_api_login_required
def api_dashboard():
    """대시보드 데이터 (잔고, 보유종목, 오늘 요약)."""
    import db as _db
    from web_admin import _safe_broker_snapshot, _parse_today_log_actions

    broker_data = _safe_broker_snapshot(user_id=g.user_id)
    today_actions = _parse_today_log_actions(user_id=g.user_id)
    env_values = _db.get_user_config(g.user_id)
    trading_mode = "실전투자" if env_values.get("IS_REAL_TRADING", "False") == "True" else "모의투자"

    return jsonify({
        "ok": True,
        "trading_mode": trading_mode,
        "balance": broker_data.get("balance", 0),
        "holdings": broker_data.get("holdings", []),
        "total_eval_amount": broker_data.get("total_eval_amount", 0),
        "total_profit_amount": broker_data.get("total_profit_amount", 0),
        "total_assets": broker_data.get("total_assets", 0),
        "broker_connected": broker_data.get("ok", False),
        "broker_error": broker_data.get("error", ""),
        "today_actions": today_actions,
    })


@api_bp.get("/holdings")
@_api_login_required
def api_holdings():
    """보유 종목 상세 조회."""
    from web_admin import _safe_broker_snapshot
    broker_data = _safe_broker_snapshot(user_id=g.user_id)
    return jsonify({
        "ok": broker_data.get("ok", False),
        "holdings": broker_data.get("holdings", []),
        "error": broker_data.get("error", ""),
    })


# ==================================================================
# 봇 관리
# ==================================================================

@api_bp.get("/bots")
@_api_login_required
def api_list_bots():
    """사용자의 봇 목록 조회."""
    import db as _db
    from bot_manager import get_bot_type_info, ensure_default_bots
    ensure_default_bots(g.user_id)
    bots = _db.get_user_bots(g.user_id)
    return jsonify({"ok": True, "bots": bots, "type_info": get_bot_type_info()})


@api_bp.post("/bots")
@_api_login_required
def api_create_bot():
    """새 봇 생성."""
    from bot_manager import create_bot_for_user
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    bot_type = str(data.get("bot_type", "buy_auto")).strip()
    config = data.get("config", {})
    if not name:
        return jsonify({"ok": False, "error": "봇 이름은 필수입니다."}), 400
    try:
        bot = create_bot_for_user(g.user_id, name, bot_type, config)
        return jsonify({"ok": True, "bot": bot})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@api_bp.get("/bots/<int:bot_id>")
@_api_login_required
def api_get_bot(bot_id: int):
    """봇 상세 조회."""
    import db as _db
    bot = _db.get_bot(bot_id)
    if not bot or bot["user_id"] != g.user_id:
        return jsonify({"ok": False, "error": "봇을 찾을 수 없습니다."}), 404
    return jsonify({"ok": True, "bot": bot})


@api_bp.put("/bots/<int:bot_id>")
@_api_login_required
def api_update_bot(bot_id: int):
    """봇 설정 수정."""
    from bot_manager import update_bot_config
    data = request.get_json(silent=True) or {}
    bot = update_bot_config(bot_id, g.user_id, **data)
    if not bot:
        return jsonify({"ok": False, "error": "봇을 찾을 수 없습니다."}), 404
    return jsonify({"ok": True, "bot": bot})


@api_bp.delete("/bots/<int:bot_id>")
@_api_login_required
def api_delete_bot(bot_id: int):
    """봇 삭제."""
    from bot_manager import delete_bot_for_user
    if delete_bot_for_user(bot_id, g.user_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "봇을 찾을 수 없습니다."}), 404


@api_bp.post("/bots/<int:bot_id>/execute")
@_api_login_required
def api_execute_bot(bot_id: int):
    """봇을 실행합니다."""
    from bot_manager import execute_bot
    data = request.get_json(silent=True) or {}
    result = execute_bot(bot_id, g.user_id, query=data.get("query", ""))
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@api_bp.post("/bots/<int:bot_id>/toggle")
@_api_login_required
def api_toggle_bot(bot_id: int):
    """봇 활성/비활성 토글."""
    import db as _db
    bot = _db.get_bot(bot_id)
    if not bot or bot["user_id"] != g.user_id:
        return jsonify({"ok": False, "error": "봇을 찾을 수 없습니다."}), 404
    new_enabled = not bot["enabled"]
    _db.update_bot(bot_id, enabled=new_enabled)
    return jsonify({"ok": True, "enabled": new_enabled})


@api_bp.get("/bots/types")
@_api_login_required
def api_bot_types():
    """사용 가능한 봇 타입 및 기본 설정."""
    from bot_manager import get_bot_type_info
    return jsonify({"ok": True, **get_bot_type_info()})


# ==================================================================
# 수동 매매
# ==================================================================

@api_bp.post("/trade/manual")
@_api_login_required
def api_manual_trade():
    """수동 매수/매도 실행."""
    from bot_manager import execute_manual_trade
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip()
    ticker = str(data.get("ticker", "")).strip()
    qty = int(data.get("qty", 0))
    bot_id = data.get("bot_id")

    if not action or not ticker or qty < 1:
        return jsonify({"ok": False, "error": "action, ticker, qty(1이상) 필수입니다."}), 400

    result = execute_manual_trade(g.user_id, action, ticker, qty, bot_id=bot_id)
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@api_bp.post("/trade/search-stock")
@_api_login_required
def api_search_stock():
    """종목명으로 티커를 검색합니다."""
    from utils import StockValidator
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "종목명을 입력하세요."}), 400
    validator = StockValidator()
    ticker = validator.verify_and_get_code(name)
    if not ticker:
        return jsonify({"ok": False, "error": f"'{name}'은 KRX 미상장 종목입니다."})
    try:
        from user_context import UserContext
        ctx = UserContext.from_user_id(g.user_id)
        price = ctx.get_broker().get_current_price(ticker)
    except Exception:
        price = 0
    return jsonify({"ok": True, "name": name, "ticker": ticker, "current_price": price})


# ==================================================================
# 체결 내역 (Trade Log)
# ==================================================================

@api_bp.get("/trades")
@_api_login_required
def api_trade_logs():
    """체결 내역 조회."""
    import db as _db
    limit = request.args.get("limit", 200, type=int)
    bot_id = request.args.get("bot_id", type=int)
    ticker = request.args.get("ticker", "").strip()
    logs = _db.get_trade_logs(g.user_id, limit=limit, bot_id=bot_id, ticker=ticker)
    return jsonify({"ok": True, "trades": logs, "count": len(logs)})


@api_bp.get("/trades/<int:trade_id>")
@_api_login_required
def api_trade_detail(trade_id: int):
    """체결 내역 상세 (AI 판단 내역 포함)."""
    import db as _db
    trade = _db.get_trade_log_by_id(trade_id)
    if not trade or trade["user_id"] != g.user_id:
        return jsonify({"ok": False, "error": "체결 내역을 찾을 수 없습니다."}), 404
    traces = []
    if trade.get("run_id"):
        traces = _db.get_traces(g.user_id, limit=50, run_id=trade["run_id"])
    return jsonify({"ok": True, "trade": trade, "ai_traces": traces})


# ==================================================================
# 모니터링
# ==================================================================

@api_bp.get("/monitor")
@_api_login_required
def api_monitor_status():
    """모니터링 상태 및 설정 조회."""
    import db as _db
    from monitor import get_monitor_state
    config = _db.get_monitor_config(g.user_id)
    state = get_monitor_state()
    user_alerts = [a for a in state.get("alerts", []) if a.get("user_id") == g.user_id]
    # 쿨다운 설정 (user_config에서 로드)
    user_cfg = _db.get_user_config(g.user_id)
    cooldown_min = int(user_cfg.get("SELL_COOLDOWN_MINUTES", "") or 180)

    return jsonify({
        "ok": True,
        "config": config or {
            "enabled": False, "check_interval_sec": 300,
            "profit_threshold": 5.0, "loss_threshold": -3.0,
            "volatility_threshold": 3.0, "auto_sell_enabled": False,
            "notify_on_threshold": True,
        },
        "sell_cooldown_minutes": cooldown_min,
        "state": {
            "running": state.get("running", False),
            "last_check": state.get("last_check", ""),
            "active_users": state.get("active_users", 0),
        },
        "recent_alerts": user_alerts[:20],
    })


@api_bp.post("/monitor")
@_api_login_required
def api_save_monitor():
    """모니터링 설정 저장."""
    import db as _db
    data = request.get_json(silent=True) or {}
    try:
        _db.save_monitor_config(
            user_id=g.user_id,
            enabled=bool(data.get("enabled", False)),
            check_interval_sec=int(data.get("check_interval_sec", 300)),
            profit_threshold=float(data.get("profit_threshold", 5.0)),
            loss_threshold=float(data.get("loss_threshold", -3.0)),
            volatility_threshold=float(data.get("volatility_threshold", 3.0)),
            auto_sell_enabled=bool(data.get("auto_sell_enabled", False)),
            notify_on_threshold=bool(data.get("notify_on_threshold", True)),
        )
        # 쿨다운 설정도 user_config에 저장
        cooldown_min = data.get("sell_cooldown_minutes")
        if cooldown_min is not None:
            _db.set_user_config(g.user_id, "SELL_COOLDOWN_MINUTES", str(int(cooldown_min)))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ==================================================================
# AI 판단 히스토리 (프롬프트 조회 포함)
# ==================================================================

@api_bp.get("/ai/traces")
@_api_login_required
def api_ai_traces():
    """AI 트레이스 목록 조회."""
    import db as _db
    limit = request.args.get("limit", 200, type=int)
    run_id = request.args.get("run_id", "").strip()
    traces = _db.get_traces(g.user_id, limit=limit, run_id=run_id)
    return jsonify({"ok": True, "traces": traces, "count": len(traces)})


@api_bp.get("/ai/traces/<run_id>")
@_api_login_required
def api_ai_trace_detail(run_id: str):
    """특정 run_id의 AI 트레이스 상세 (프롬프트 포함)."""
    import db as _db
    traces = _db.get_traces(g.user_id, limit=100, run_id=run_id)
    if not traces:
        return jsonify({"ok": False, "error": "해당 실행 기록을 찾을 수 없습니다."}), 404

    prompts = []
    decisions = []
    for t in traces:
        payload = t.get("payload", {})
        if payload.get("prompt"):
            prompts.append({
                "event_type": t["event_type"],
                "ai_model": payload.get("ai_model", ""),
                "prompt": payload["prompt"],
                "time": t.get("time", ""),
            })
        if t["event_type"] in ("sell_ai_decision", "buy_recommendation", "sell_final_decision"):
            decisions.append({
                "event_type": t["event_type"],
                "ai_model": payload.get("ai_model", ""),
                "action": payload.get("action", payload.get("stock_name", "")),
                "reason": payload.get("reason", ""),
                "time": t.get("time", ""),
            })

    return jsonify({
        "ok": True,
        "run_id": run_id,
        "traces": traces,
        "prompts": prompts,
        "decisions": decisions,
        "mode": traces[0].get("mode", "") if traces else "",
        "trading_mode": traces[0].get("trading_mode", "") if traces else "",
    })


@api_bp.get("/ai/runs")
@_api_login_required
def api_ai_runs():
    """AI 실행 이력 (run_id별 요약)."""
    import db as _db
    traces = _db.get_traces(g.user_id, limit=500)

    runs: dict = {}
    for t in traces:
        rid = t.get("run_id", "")
        if not rid:
            continue
        if rid not in runs:
            runs[rid] = {
                "run_id": rid,
                "mode": t.get("mode", ""),
                "trading_mode": t.get("trading_mode", ""),
                "started_at": t.get("time", ""),
                "ended_at": t.get("time", ""),
                "event_count": 0,
                "has_prompts": False,
            }
        run = runs[rid]
        run["event_count"] += 1
        if t.get("time", "") < run["started_at"]:
            run["started_at"] = t["time"]
        if t.get("time", "") > run["ended_at"]:
            run["ended_at"] = t["time"]
        if t.get("payload", {}).get("prompt"):
            run["has_prompts"] = True

    run_list = sorted(runs.values(), key=lambda r: r["ended_at"], reverse=True)[:50]
    return jsonify({"ok": True, "runs": run_list})


# ==================================================================
# 액션 실행 (매수/매도/현황)
# ==================================================================

@api_bp.post("/actions/run")
@_api_login_required
def api_run_action():
    """매수/매도/현황 실행 (비동기)."""
    from web_admin import AI_RUN_LOCK, AI_RUN_STATE, _run_ai_action_in_background
    data = request.get_json(silent=True) or {}
    mode = str(data.get("action", "")).strip().lower()
    if mode not in ("buy", "sell", "status"):
        return jsonify({"ok": False, "error": "지원하지 않는 액션입니다."}), 400

    with AI_RUN_LOCK:
        if AI_RUN_STATE["running"]:
            return jsonify({
                "ok": False,
                "error": "이미 실행 중입니다.",
                "state": dict(AI_RUN_STATE),
            }), 409

    run_id = uuid.uuid4().hex[:12]
    thread = threading.Thread(
        target=_run_ai_action_in_background,
        args=(mode, run_id, g.user_id),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "mode": mode})


@api_bp.get("/actions/state")
@_api_login_required
def api_action_state():
    """현재 AI 실행 상태."""
    from web_admin import _snapshot_ai_state
    return jsonify({"ok": True, "state": _snapshot_ai_state()})


@api_bp.get("/actions/history")
@_api_login_required
def api_action_history():
    """액션 실행 히스토리."""
    import db as _db
    limit = request.args.get("limit", 200, type=int)
    rows = _db.get_actions(g.user_id, limit=limit)
    return jsonify({"ok": True, "history": rows})


# ==================================================================
# 설정
# ==================================================================

@api_bp.get("/settings")
@_api_login_required
def api_get_settings():
    """사용자 설정 조회 (민감값 마스킹)."""
    import db as _db
    from web_admin import _mask_value
    values = _db.get_user_config(g.user_id)
    masked = {k: _mask_value(k, v) for k, v in values.items()}
    return jsonify({"ok": True, "values": values, "masked": masked})


@api_bp.post("/settings")
@_api_login_required
def api_save_settings():
    """사용자 설정 저장."""
    import db as _db
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "저장할 설정이 없습니다."}), 400
    _db.set_user_config_bulk(g.user_id, data)
    return jsonify({"ok": True})


# ==================================================================
# 종목 질문 (Ask)
# ==================================================================

@api_bp.post("/ask")
@_api_login_required
def api_ask_stock():
    """AI에게 종목 질문."""
    from bot_service import execute_mode
    data = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()
    if not query:
        return jsonify({"ok": False, "error": "질문을 입력해 주세요."}), 400
    try:
        result = execute_mode("ask", query=query, user_id=g.user_id)
        return jsonify({"ok": result["ok"], "output": result.get("output", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ==================================================================
# 스케줄
# ==================================================================

@api_bp.get("/schedule")
@_api_login_required
def api_get_schedule():
    """스케줄 설정 조회."""
    from web_admin import _load_schedule_snapshot
    try:
        snapshot = _load_schedule_snapshot(user_id=g.user_id)
        return jsonify({"ok": True, "schedule": snapshot})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.post("/schedule")
@_api_login_required
def api_save_schedule():
    """스케줄 설정 저장."""
    import db as _db
    from web_admin import _normalize_hhmm_list, _normalize_weekdays
    data = request.get_json(silent=True) or {}
    try:
        buy_times = _normalize_hhmm_list(str(data.get("buy_times", "")))
        sell_times = _normalize_hhmm_list(str(data.get("sell_times", "")))
        weekdays = _normalize_weekdays(str(data.get("weekdays", "1-5")))
        _db.save_schedule_config(
            user_id=g.user_id,
            enabled=bool(data.get("enabled", False)),
            weekdays=weekdays,
            timezone=str(data.get("timezone", "Asia/Seoul")).strip(),
            buy_times=buy_times,
            sell_times=sell_times,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ==================================================================
# 관리자 API
# ==================================================================

@api_bp.get("/admin/users")
@_api_admin_required
def api_admin_users():
    import db as _db
    return jsonify({"ok": True, "users": _db.get_all_users()})


@api_bp.post("/admin/users")
@_api_admin_required
def api_admin_create_user():
    import db as _db
    from auth import hash_password
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    display_name = str(data.get("display_name", "")).strip() or username
    is_admin = bool(data.get("is_admin", False))
    if not username or not password:
        return jsonify({"ok": False, "error": "사용자명과 비밀번호는 필수입니다."}), 400
    try:
        uid = _db.create_user(username, hash_password(password), display_name, is_admin)
        return jsonify({"ok": True, "user_id": uid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
