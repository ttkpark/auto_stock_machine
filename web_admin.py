"""
웹 기반 통합 대시보드 + 환경 변수 관리자

실행:
    python web_admin.py

접속:
    http://127.0.0.1:5000
"""
import json
import os
import platform
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from bot_service import execute_mode


BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
LOG_PATH = BASE_DIR / "logs" / "bot.log"
ACTION_HISTORY_PATH = BASE_DIR / "data" / "web_actions.json"
AI_TRACE_PATH = BASE_DIR / "data" / "ai_traces.jsonl"
APP_STARTED_AT = time.time()
AI_RUN_LOCK = threading.Lock()
AI_RUN_STATE: Dict[str, Any] = {
    "running": False,
    "run_id": "",
    "mode": "",
    "started_at": "",
    "finished_at": "",
    "last_result": None,
}

# 웹에서 관리할 환경 변수 목록
MANAGED_KEYS: List[str] = [
    "IS_REAL_TRADING",
    "BUY_BUDGET_RATIO",
    "MIN_AI_CONSENSUS",
    "TAKE_PROFIT_RATE",
    "STOP_LOSS_RATE",
    "GEMINI_API_KEY",
    "CLAUDE_API_KEY",
    "KIS_MOCK_APP_KEY",
    "KIS_MOCK_APP_SECRET",
    "KIS_MOCK_ACCOUNT_NUMBER",
    "KIS_REAL_APP_KEY",
    "KIS_REAL_APP_SECRET",
    "KIS_REAL_ACCOUNT_NUMBER",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def _mask_value(key: str, value: str) -> str:
    """민감 값을 UI에서 부분 마스킹합니다."""
    sensitive_keywords = ("KEY", "SECRET", "TOKEN")
    if any(word in key for word in sensitive_keywords):
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
    return value


def _read_env_lines() -> List[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _load_env_values() -> Dict[str, str]:
    load_dotenv(ENV_PATH, override=True)
    values: Dict[str, str] = {}
    for key in MANAGED_KEYS:
        values[key] = os.environ.get(key, "")
    return values


def _save_env_values(new_values: Dict[str, str]) -> None:
    """
    .env 파일의 기존 구조(주석/순서)를 최대한 유지하면서 값만 교체합니다.
    파일에 없던 키는 맨 아래에 추가합니다.
    """
    lines = _read_env_lines()
    seen = set()
    updated_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue

        key, _old = line.split("=", 1)
        key = key.strip()
        if key in new_values:
            updated_lines.append(f"{key}={new_values[key]}")
            seen.add(key)
        else:
            updated_lines.append(line)

    for key in MANAGED_KEYS:
        if key in new_values and key not in seen:
            updated_lines.append(f"{key}={new_values[key]}")

    ENV_PATH.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    # 저장 후 현재 프로세스 환경에도 즉시 반영
    load_dotenv(ENV_PATH, override=True)


def _is_logged_in() -> bool:
    return bool(session.get("admin_authenticated"))


def _login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for("login_page"))
        return func(*args, **kwargs)

    return wrapper


def _format_won(value: int) -> str:
    return f"{value:,}원"


def _calc_profit_amount(holding: Dict[str, Any]) -> int:
    return (holding.get("current_price", 0) - holding.get("avg_price", 0)) * holding.get("qty", 0)


def _safe_broker_snapshot() -> Dict[str, Any]:
    """
    브로커 연결 상태/잔고/보유종목을 조회합니다.
    실패해도 대시보드가 죽지 않도록 예외를 흡수합니다.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "error": "",
        "balance": 0,
        "holdings": [],
        "total_eval_amount": 0,
        "total_profit_amount": 0,
    }

    try:
        if _read_bool_env("IS_REAL_TRADING", False):
            from brokers import RealBroker
            broker = RealBroker()
        else:
            from brokers import MockBroker
            broker = MockBroker()
        balance = broker.get_balance()
        holdings = broker.get_holdings()
        total_eval = 0
        total_profit = 0
        for h in holdings:
            eval_amount = h.get("current_price", 0) * h.get("qty", 0)
            profit_amount = _calc_profit_amount(h)
            h["eval_amount"] = eval_amount
            h["profit_amount"] = profit_amount
            total_eval += eval_amount
            total_profit += profit_amount

        result.update(
            {
                "ok": True,
                "balance": balance,
                "holdings": holdings,
                "total_eval_amount": total_eval,
                "total_profit_amount": total_profit,
            }
        )
    except Exception as e:
        result["error"] = str(e)

    return result


def _load_action_history() -> List[Dict[str, Any]]:
    if not ACTION_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(ACTION_HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _append_action_history(action: str, status: str, detail: str) -> None:
    ACTION_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = _load_action_history()
    rows.append(
        {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "status": status,
            "detail": detail,
        }
    )
    # 최근 200개만 유지
    rows = rows[-200:]
    ACTION_HISTORY_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_today_log_actions() -> List[str]:
    """
    오늘 날짜 기준 핵심 로그를 추려서 액션 리스트로 반환합니다.
    """
    if not LOG_PATH.exists():
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    lines = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    keywords = ("[매수", "[매도", "[현황", "최종 결정", "손절", "치명적 오류")
    filtered: List[str] = []
    for line in lines:
        if today in line and any(k in line for k in keywords):
            filtered.append(line)
    return filtered[-80:]


def _load_ai_traces(limit: int = 200, run_id: str = "") -> List[Dict[str, Any]]:
    if not AI_TRACE_PATH.exists():
        return []

    rows: List[Dict[str, Any]] = []
    for raw in AI_TRACE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if run_id and row.get("run_id") != run_id:
            continue
        rows.append(row)

    return rows[-limit:]


def _snapshot_ai_state() -> Dict[str, Any]:
    with AI_RUN_LOCK:
        return dict(AI_RUN_STATE)


def _run_ai_action_in_background(mode: str, run_id: str) -> None:
    with AI_RUN_LOCK:
        AI_RUN_STATE.update(
            {
                "running": True,
                "run_id": run_id,
                "mode": mode,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": "",
                "last_result": None,
            }
        )

    result = _run_bot_mode(mode, run_id=run_id)
    with AI_RUN_LOCK:
        AI_RUN_STATE["running"] = False
        AI_RUN_STATE["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        AI_RUN_STATE["last_result"] = result


def _run_bot_mode(mode: str, run_id: str = "") -> Dict[str, Any]:
    """
    백엔드 매매 모드를 웹에서 직접 실행합니다.
    """
    try:
        result = execute_mode(mode, run_id=run_id or None)
        ok = result["ok"]
        output_preview = result.get("output", "(출력 없음)")
        resolved_run_id = result.get("run_id", run_id)
        history_detail = f"[run_id={resolved_run_id}] {output_preview}" if resolved_run_id else output_preview
        _append_action_history(mode, "success" if ok else "failed", history_detail)
        return {
            "ok": ok,
            "returncode": result["returncode"],
            "output": output_preview,
            "run_id": resolved_run_id,
        }
    except Exception as e:
        history_detail = f"[run_id={run_id}] {e}" if run_id else str(e)
        _append_action_history(mode, "failed", history_detail)
        return {"ok": False, "returncode": -1, "output": str(e), "run_id": run_id}


def _server_status_snapshot() -> Dict[str, Any]:
    uptime_sec = int(time.time() - APP_STARTED_AT)
    log_mtime = "-"
    if LOG_PATH.exists():
        log_mtime = datetime.fromtimestamp(LOG_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "host": "127.0.0.1:5000",
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "cwd": str(BASE_DIR),
        "uptime_sec": uptime_sec,
        "last_log_update": log_mtime,
        "venv": sys.prefix,
    }


def _read_bool_env(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() == "true"


def create_app() -> Flask:
    app = Flask(__name__)
    # 빈 문자열("")이 들어온 경우도 안전하게 랜덤 키를 사용합니다.
    app.config["SECRET_KEY"] = os.environ.get("WEB_ADMIN_SESSION_SECRET") or secrets.token_hex(24)

    @app.get("/login")
    def login_page():
        return render_template("login.html")

    @app.post("/login")
    def login():
        password = request.form.get("password", "")
        expected_password = os.environ.get("WEB_ADMIN_PASSWORD", "")
        if not expected_password:
            flash("WEB_ADMIN_PASSWORD가 설정되지 않았습니다. .env에 먼저 설정해 주세요.", "error")
            return redirect(url_for("login_page"))
        if password != expected_password:
            flash("비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("login_page"))

        session["admin_authenticated"] = True
        return redirect(url_for("dashboard"))

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page"))

    @app.get("/")
    @_login_required
    def dashboard():
        broker_data = _safe_broker_snapshot()
        env_values = _load_env_values()
        server = _server_status_snapshot()
        today_actions = _parse_today_log_actions()
        recent_history = _load_action_history()[-20:][::-1]

        return render_template(
            "dashboard.html",
            trading_mode="실전투자" if _read_bool_env("IS_REAL_TRADING", False) else "모의투자",
            env_values=env_values,
            broker_data=broker_data,
            server=server,
            today_actions=today_actions,
            recent_history=recent_history,
            format_won=_format_won,
        )

    @app.post("/toggle-trading")
    @_login_required
    def toggle_trading():
        values = _load_env_values()
        current = values.get("IS_REAL_TRADING", "False")
        values["IS_REAL_TRADING"] = "False" if current == "True" else "True"
        _save_env_values(values)
        mode_txt = "실전투자" if values["IS_REAL_TRADING"] == "True" else "모의투자"
        flash(f"투자 모드를 {mode_txt}로 변경했습니다. (다음 실행부터 반영)", "success")
        return redirect(url_for("dashboard"))

    @app.get("/settings")
    @_login_required
    def settings():
        values = _load_env_values()
        masked_values = {k: _mask_value(k, v) for k, v in values.items()}
        return render_template("settings.html", values=values, masked_values=masked_values, managed_keys=MANAGED_KEYS)

    @app.post("/settings/save")
    @_login_required
    def settings_save():
        current_values = _load_env_values()
        new_values: Dict[str, str] = {}
        for key in MANAGED_KEYS:
            value = request.form.get(key, "").strip()

            # 값이 비어 있는데 기존값이 있었고, 마스킹 문자열이 들어왔다면 기존값 유지
            masked_current = _mask_value(key, current_values.get(key, ""))
            if value == masked_current and current_values.get(key, ""):
                value = current_values[key]

            new_values[key] = value

        # 불리언 기본 검증
        if new_values["IS_REAL_TRADING"] not in {"True", "False"}:
            flash("IS_REAL_TRADING 값은 True 또는 False 여야 합니다.", "error")
            return redirect(url_for("settings"))

        _save_env_values(new_values)
        flash("설정이 저장되었습니다. 다음 실행부터 반영됩니다.", "success")
        return redirect(url_for("settings"))

    @app.get("/actions")
    @_login_required
    def actions():
        history = _load_action_history()[::-1]
        today_actions = _parse_today_log_actions()[::-1]
        return render_template("actions.html", history=history, today_actions=today_actions)

    @app.get("/ai")
    @_login_required
    def ai_page():
        state = _snapshot_ai_state()
        traces = _load_ai_traces(limit=200, run_id=state.get("run_id", ""))
        return render_template("ai.html", state=state, traces=traces)

    @app.get("/api/ai/state")
    @_login_required
    def api_ai_state():
        state = _snapshot_ai_state()
        run_id = request.args.get("run_id", "").strip() or state.get("run_id", "")
        traces = _load_ai_traces(limit=200, run_id=run_id)
        return jsonify({"state": state, "traces": traces, "active_run_id": run_id})

    @app.post("/api/ai/run")
    @_login_required
    def api_ai_run():
        mode = request.form.get("action", "").strip().lower()
        if not mode and request.is_json:
            mode = str((request.get_json(silent=True) or {}).get("action", "")).strip().lower()
        if mode not in {"buy", "sell", "status"}:
            return jsonify({"ok": False, "message": "지원하지 않는 액션입니다."}), 400

        with AI_RUN_LOCK:
            if AI_RUN_STATE["running"]:
                return jsonify(
                    {
                        "ok": False,
                        "message": "이미 실행 중입니다. 현재 작업이 끝난 뒤 다시 시도하세요.",
                        "state": dict(AI_RUN_STATE),
                    }
                ), 409

        run_id = uuid.uuid4().hex[:12]
        thread = threading.Thread(target=_run_ai_action_in_background, args=(mode, run_id), daemon=True)
        thread.start()
        return jsonify({"ok": True, "run_id": run_id, "mode": mode})

    @app.post("/actions/run")
    @_login_required
    def run_action():
        action = request.form.get("action", "").strip().lower()
        if action not in {"buy", "sell", "status"}:
            flash("지원하지 않는 액션입니다.", "error")
            return redirect(url_for("actions"))

        result = _run_bot_mode(action)
        if result["ok"]:
            flash(f"{action} 실행 완료 (rc={result['returncode']})", "success")
        else:
            flash(f"{action} 실행 실패 (rc={result['returncode']})", "error")
        return redirect(url_for("actions"))

    @app.get("/server")
    @_login_required
    def server():
        status = _server_status_snapshot()
        return render_template("server.html", status=status)

    return app


def run_web_admin(host: str = "127.0.0.1", port: int = 5000, debug: bool = False) -> None:
    """웹 관리자 서버를 실행합니다."""
    load_dotenv(ENV_PATH, override=False)
    app = create_app()
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_web_admin()
