"""
웹 기반 통합 대시보드 + 환경 변수 관리자

실행:
    python web_admin.py

접속:
    http://127.0.0.1:8004 (또는 LAN의 http://<이_PC_IP>:8004)
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
from zoneinfo import ZoneInfo

import config as _cfg

from dotenv import load_dotenv
from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for

from bot_service import execute_mode


BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"

# Flask 기본 리슨 (외부에서 접속하려면 0.0.0.0)
DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 8004
_RUNTIME_LISTEN: tuple[str, int] = (DEFAULT_WEB_HOST, DEFAULT_WEB_PORT)
LOG_PATH = BASE_DIR / "logs" / "bot.log"
ACTION_HISTORY_PATH = BASE_DIR / "data" / "web_actions.json"
AI_TRACE_PATH = BASE_DIR / "data" / "ai_traces.jsonl"
SCHEDULE_CONFIG_PATH = BASE_DIR / "data" / "web_schedule.json"
PROMPTS_PATH = BASE_DIR / "data" / "prompts.json"
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
SCHEDULER_STATE_LOCK = threading.Lock()
SCHEDULER_STATE: Dict[str, Any] = {
    "running": False,
    "last_tick": "",
    "last_message": "",
    "last_triggered": {"buy": "", "sell": ""},
}
_SCHEDULER_STARTED = False

# AI 엔진별 선택 가능한 모델 목록
MODEL_OPTIONS: Dict[str, List[str]] = {
    "GEMINI_MODEL_NAME": [
        "gemini-2.0-flash",
        "gemini-2.5-flash-preview-05-20",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ],
    "CLAUDE_MODEL_NAME": [
        "claude-haiku-4-5-latest",
        "claude-sonnet-4-5-latest",
        "claude-opus-4-6-latest",
    ],
    "OPENAI_MODEL_NAME": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4-turbo",
    ],
}

# 웹에서 관리할 환경 변수 목록
MANAGED_KEYS: List[str] = [
    "IS_REAL_TRADING",
    "APP_TIMEZONE",
    "BUY_BUDGET_RATIO",
    "MIN_AI_CONSENSUS",
    "TAKE_PROFIT_RATE",
    "STOP_LOSS_RATE",
    "GEMINI_API_KEY",
    "GEMINI_MODEL_NAME",
    "CLAUDE_API_KEY",
    "CLAUDE_MODEL_NAME",
    "OPENAI_API_KEY",
    "OPENAI_MODEL_NAME",
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
    return bool(session.get("user_id"))


def _login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for("login_page"))
        g.user_id = session.get("user_id", 0)
        g.username = session.get("username", "")
        g.is_admin = session.get("is_admin", False)
        return func(*args, **kwargs)

    return wrapper


def _admin_required(func):
    @wraps(func)
    @_login_required
    def wrapper(*args, **kwargs):
        if not g.is_admin:
            flash("관리자 권한이 필요합니다.", "error")
            return redirect(url_for("dashboard"))
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
        "total_assets": 0,
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
                "total_assets": balance + total_eval,
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
            "time": _cfg.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    today = _cfg.now().strftime("%Y-%m-%d")
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


def _run_ai_action_in_background(mode: str, run_id: str, user_id: int = 0) -> None:
    with AI_RUN_LOCK:
        AI_RUN_STATE.update(
            {
                "running": True,
                "run_id": run_id,
                "mode": mode,
                "user_id": user_id,
                "started_at": _cfg.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": "",
                "last_result": None,
            }
        )

    result = _run_bot_mode(mode, run_id=run_id, user_id=user_id)
    with AI_RUN_LOCK:
        AI_RUN_STATE["running"] = False
        AI_RUN_STATE["finished_at"] = _cfg.now().strftime("%Y-%m-%d %H:%M:%S")
        AI_RUN_STATE["last_result"] = result


def _run_bot_mode(mode: str, run_id: str = "", user_id: int = 0) -> Dict[str, Any]:
    """
    백엔드 매매 모드를 웹에서 직접 실행합니다.
    """
    try:
        result = execute_mode(mode, run_id=run_id or None, user_id=user_id)
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
        from datetime import datetime as _dt
        tz = ZoneInfo(_cfg.APP_TIMEZONE)
        log_mtime = _dt.fromtimestamp(LOG_PATH.stat().st_mtime, tz=tz).strftime("%Y-%m-%d %H:%M:%S")
    h, p = _RUNTIME_LISTEN
    return {
        "host": f"{h}:{p}",
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "cwd": str(BASE_DIR),
        "uptime_sec": uptime_sec,
        "last_log_update": log_mtime,
        "venv": sys.prefix,
    }


# ── AI 사용량 모니터링 ──────────────────────────────────────


def _count_today_ai_calls() -> Dict[str, int]:
    """오늘자 ai_traces.jsonl 에서 AI 엔진별 API 호출 횟수를 집계합니다."""
    today_str = _cfg.now().strftime("%Y-%m-%d")
    counts: Dict[str, int] = {"Gemini": 0, "Claude": 0, "ChatGPT": 0}
    if not AI_TRACE_PATH.exists():
        return counts
    try:
        for line in AI_TRACE_PATH.read_text(encoding="utf-8").splitlines():
            if today_str not in line:
                continue
            entry = json.loads(line)
            analyzer = entry.get("payload", {}).get("analyzer", "")
            if "Gemini" in analyzer:
                counts["Gemini"] += 1
            elif "Claude" in analyzer:
                counts["Claude"] += 1
            elif "OpenAI" in analyzer:
                counts["ChatGPT"] += 1
    except Exception:
        pass
    return counts


def _format_number(val: str) -> str:
    """숫자 문자열에 천 단위 콤마를 추가합니다."""
    try:
        return f"{int(val):,}"
    except (ValueError, TypeError):
        return val


def _check_anthropic_usage() -> Dict[str, Any]:
    """Anthropic Claude API 상태 및 rate limit 를 확인합니다."""
    import requests as req

    key = os.environ.get("CLAUDE_API_KEY", "")
    if not key:
        return {"provider": "Anthropic Claude", "status": "not_configured"}
    model = os.environ.get("CLAUDE_MODEL_NAME", "").strip() or "claude-haiku-4-5-latest"
    try:
        resp = req.post(
            "https://api.anthropic.com/v1/messages/count_tokens",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
            timeout=10,
        )
        if resp.status_code == 200:
            rl = {
                "요청 한도": _format_number(resp.headers.get("anthropic-ratelimit-requests-limit", "-")),
                "남은 요청": _format_number(resp.headers.get("anthropic-ratelimit-requests-remaining", "-")),
                "토큰 한도": _format_number(resp.headers.get("anthropic-ratelimit-tokens-limit", "-")),
                "남은 토큰": _format_number(resp.headers.get("anthropic-ratelimit-tokens-remaining", "-")),
            }
            return {"provider": "Anthropic Claude", "status": "active", "model": model, "rate_limits": rl}
        return {"provider": "Anthropic Claude", "status": "error", "model": model, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"provider": "Anthropic Claude", "status": "error", "model": model, "error": str(e)[:200]}


def _check_openai_usage() -> Dict[str, Any]:
    """OpenAI API 상태를 확인합니다."""
    import requests as req

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {"provider": "OpenAI ChatGPT", "status": "not_configured"}
    model = os.environ.get("OPENAI_MODEL_NAME", "").strip() or "gpt-4o-mini"
    try:
        # 최소 completion 요청으로 rate limit 헤더 확보
        resp = req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            rl = {}
            for hdr, label in [
                ("x-ratelimit-limit-requests", "요청 한도"),
                ("x-ratelimit-remaining-requests", "남은 요청"),
                ("x-ratelimit-limit-tokens", "토큰 한도"),
                ("x-ratelimit-remaining-tokens", "남은 토큰"),
            ]:
                val = resp.headers.get(hdr, "-")
                rl[label] = _format_number(val)
            return {"provider": "OpenAI ChatGPT", "status": "active", "model": model, "rate_limits": rl}
        return {"provider": "OpenAI ChatGPT", "status": "error", "model": model, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"provider": "OpenAI ChatGPT", "status": "error", "model": model, "error": str(e)[:200]}


def _check_gemini_usage() -> Dict[str, Any]:
    """Google Gemini API 상태를 확인합니다."""
    import requests as req

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"provider": "Google Gemini", "status": "not_configured"}
    model = os.environ.get("GEMINI_MODEL_NAME", "").strip() or "gemini-2.0-flash"
    try:
        resp = req.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=10,
        )
        if resp.status_code == 200:
            return {"provider": "Google Gemini", "status": "active", "model": model, "rate_limits": None}
        return {"provider": "Google Gemini", "status": "error", "model": model, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"provider": "Google Gemini", "status": "error", "model": model, "error": str(e)[:200]}


def _check_all_ai_usage() -> Dict[str, Any]:
    """모든 AI 제공자의 상태와 오늘 호출 횟수를 종합합니다."""
    providers = []
    for check_fn in (_check_anthropic_usage, _check_openai_usage, _check_gemini_usage):
        providers.append(check_fn())
    today_calls = _count_today_ai_calls()
    label_map = {"Anthropic Claude": "Claude", "OpenAI ChatGPT": "ChatGPT", "Google Gemini": "Gemini"}
    for p in providers:
        p["today_calls"] = today_calls.get(label_map.get(p["provider"], ""), 0)
    return {"providers": providers, "checked_at": _cfg.now().strftime("%Y-%m-%d %H:%M:%S")}


# ── 동적 모델 목록 조회 ─────────────────────────────────────


def _fetch_anthropic_models() -> List[str]:
    """Anthropic API에서 사용 가능한 모델 목록을 가져옵니다."""
    import requests as req

    key = os.environ.get("CLAUDE_API_KEY", "")
    if not key:
        return []
    try:
        resp = req.get(
            "https://api.anthropic.com/v1/models?limit=100",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        models = [m["id"] for m in data.get("data", []) if m.get("id")]
        # claude 모델만 필터, 정렬 (최신순)
        models = sorted([m for m in models if "claude" in m], reverse=True)
        return models
    except Exception:
        return []


def _fetch_openai_models() -> List[str]:
    """OpenAI API에서 사용 가능한 GPT 모델 목록을 가져옵니다."""
    import requests as req

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return []
    try:
        resp = req.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        models = [m["id"] for m in data.get("data", []) if m.get("id")]
        # gpt 모델만 필터, 정렬
        models = sorted([m for m in models if m.startswith("gpt")], reverse=True)
        return models
    except Exception:
        return []


def _fetch_gemini_models() -> List[str]:
    """Google Gemini API에서 사용 가능한 모델 목록을 가져옵니다."""
    import requests as req

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return []
    try:
        resp = req.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")  # "models/gemini-2.0-flash"
            if name.startswith("models/"):
                name = name[len("models/"):]
            # generateContent 를 지원하는 모델만
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods and "gemini" in name:
                models.append(name)
        return sorted(models, reverse=True)
    except Exception:
        return []


def _fetch_all_available_models() -> Dict[str, List[str]]:
    """모든 AI 제공자에서 사용 가능한 모델 목록을 가져옵니다."""
    return {
        "CLAUDE_MODEL_NAME": _fetch_anthropic_models(),
        "OPENAI_MODEL_NAME": _fetch_openai_models(),
        "GEMINI_MODEL_NAME": _fetch_gemini_models(),
    }


def _read_bool_env(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() == "true"


def _split_time_tokens(raw: str) -> List[str]:
    normalized = raw.replace(";", ",").replace("\n", ",")
    return [t.strip() for t in normalized.split(",") if t.strip()]


def _normalize_hhmm_list(raw: str) -> List[str]:
    tokens = _split_time_tokens(raw)
    if not tokens:
        return []
    validated: List[str] = []
    for token in tokens:
        parts = token.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f"시간 형식 오류: {token} (예: 09:05)")
        hh = int(parts[0])
        mm = int(parts[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"시간 범위 오류: {token}")
        validated.append(f"{hh:02d}:{mm:02d}")
    return sorted(set(validated))


def _normalize_weekdays(raw: str) -> str:
    token = raw.strip().replace(" ", "")
    if not token:
        raise ValueError("요일은 비워둘 수 없습니다. 예: 1-5")
    allowed = set("1234567,-")
    if any(ch not in allowed for ch in token):
        raise ValueError("요일 형식이 올바르지 않습니다. 예: 1-5 또는 1,2,3,4,5")
    return token


def _default_schedule_config() -> Dict[str, Any]:
    default_tz = os.environ.get("APP_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"
    return {
        "enabled": False,
        "weekdays": "1-5",
        "timezone": default_tz,
        "buy_times": ["09:05", "11:05", "13:05", "15:05"],
        "sell_times": ["09:35", "11:35", "13:35", "15:25"],
    }


def _load_schedule_config() -> Dict[str, Any]:
    cfg = _default_schedule_config()
    if not SCHEDULE_CONFIG_PATH.exists():
        return cfg
    try:
        loaded = json.loads(SCHEDULE_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cfg.update(loaded)
    except Exception:
        return cfg
    return cfg


def _save_schedule_config(
    enabled: bool,
    weekdays: str,
    timezone_name: str,
    buy_times: List[str],
    sell_times: List[str],
) -> None:
    if not timezone_name.strip():
        raise ValueError("타임존은 비워둘 수 없습니다. 예: Asia/Seoul")
    validated_weekdays = _normalize_weekdays(weekdays)
    config = {
        "enabled": bool(enabled),
        "weekdays": validated_weekdays,
        "timezone": timezone_name.strip(),
        "buy_times": buy_times,
        "sell_times": sell_times,
    }
    SCHEDULE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _current_time_in_timezone(timezone_name: str):
    try:
        return _cfg.now()
    except Exception:
        from datetime import datetime
        return datetime.now(ZoneInfo("Asia/Seoul"))


def _time_due(now: datetime, weekdays: str, hhmm_list: List[str]) -> bool:
    weekday_token = str(now.isoweekday())
    allowed_days: List[str] = []
    for chunk in weekdays.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            if left.isdigit() and right.isdigit():
                start = int(left)
                end = int(right)
                if 1 <= start <= end <= 7:
                    allowed_days.extend([str(d) for d in range(start, end + 1)])
            continue
        if part.isdigit() and 1 <= int(part) <= 7:
            allowed_days.append(part)
    if weekday_token not in set(allowed_days):
        return False
    return now.strftime("%H:%M") in set(hhmm_list)


def _run_scheduled_mode(mode: str, timezone_name: str, triggered_at: datetime) -> None:
    with AI_RUN_LOCK:
        if AI_RUN_STATE["running"]:
            _append_action_history(mode, "skipped", f"[scheduler] {mode} 스킵: 이미 실행 중")
            return

    run_id = uuid.uuid4().hex[:12]
    _append_action_history(
        mode,
        "queued",
        f"[scheduler] {timezone_name} {triggered_at.strftime('%Y-%m-%d %H:%M')} 트리거 (run_id={run_id})",
    )
    thread = threading.Thread(target=_run_ai_action_in_background, args=(mode, run_id), daemon=True)
    thread.start()


def _scheduler_loop() -> None:
    fired_keys: set[str] = set()
    while True:
        try:
            config = _load_schedule_config()
            now = _current_time_in_timezone(str(config.get("timezone", "Asia/Seoul")))
            enabled = bool(config.get("enabled", False))
            weekdays = str(config.get("weekdays", "1-5"))
            buy_times = [str(v) for v in config.get("buy_times", [])]
            sell_times = [str(v) for v in config.get("sell_times", [])]

            with SCHEDULER_STATE_LOCK:
                SCHEDULER_STATE["running"] = True
                SCHEDULER_STATE["last_tick"] = now.strftime("%Y-%m-%d %H:%M:%S")
                SCHEDULER_STATE["last_message"] = "활성" if enabled else "비활성"

            if enabled:
                due_modes: List[str] = []
                if _time_due(now, weekdays, buy_times):
                    due_modes.append("buy")
                if _time_due(now, weekdays, sell_times):
                    due_modes.append("sell")

                for mode in due_modes:
                    fire_key = f"{mode}:{now.strftime('%Y-%m-%d %H:%M')}"
                    if fire_key in fired_keys:
                        continue
                    fired_keys.add(fire_key)
                    if len(fired_keys) > 1000:
                        fired_keys = set(list(fired_keys)[-400:])
                    with SCHEDULER_STATE_LOCK:
                        SCHEDULER_STATE["last_triggered"][mode] = now.strftime("%Y-%m-%d %H:%M:%S")
                    _run_scheduled_mode(mode, str(config.get("timezone", "Asia/Seoul")), now)
        except Exception as e:
            with SCHEDULER_STATE_LOCK:
                SCHEDULER_STATE["last_message"] = f"오류: {e}"
        time.sleep(10)


def _ensure_scheduler_started() -> None:
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    _SCHEDULER_STARTED = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()


def _load_schedule_snapshot() -> Dict[str, Any]:
    config = _load_schedule_config()
    with SCHEDULER_STATE_LOCK:
        state = {
            "running": SCHEDULER_STATE.get("running", False),
            "last_tick": SCHEDULER_STATE.get("last_tick", ""),
            "last_message": SCHEDULER_STATE.get("last_message", ""),
            "last_triggered": dict(SCHEDULER_STATE.get("last_triggered", {"buy": "", "sell": ""})),
        }
    now = _current_time_in_timezone(str(config.get("timezone", "Asia/Seoul")))
    return {
        "supported": True,
        "enabled": bool(config.get("enabled", False)),
        "weekdays": str(config.get("weekdays", "1-5")),
        "timezone": str(config.get("timezone", "Asia/Seoul")),
        "buy_times": [str(v) for v in config.get("buy_times", [])],
        "sell_times": [str(v) for v in config.get("sell_times", [])],
        "source": str(SCHEDULE_CONFIG_PATH),
        "scheduler_state": state,
        "now_in_timezone": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("WEB_ADMIN_SESSION_SECRET") or secrets.token_hex(24)

    from datetime import timedelta
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

    # DB 초기화 (자동 마이그레이션 포함)
    try:
        import db as db_module
        db_module.init_db()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DB 초기화 실패, 레거시 모드로 동작: {e}")

    _ensure_scheduler_started()

    @app.get("/login")
    def login_page():
        return render_template("login.html")

    @app.post("/login")
    def login():
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # DB 인증 시도
        try:
            import db as db_module
            from auth import verify_password
            if db_module.is_db_available() and username:
                user = db_module.get_user_by_username(username)
                if user and user["is_active"] and verify_password(password, user["password_hash"]):
                    session.permanent = True
                    session["user_id"] = user["id"]
                    session["username"] = user["username"]
                    session["is_admin"] = bool(user["is_admin"])
                    return redirect(url_for("dashboard"))
                flash("사용자명 또는 비밀번호가 일치하지 않습니다.", "error")
                return redirect(url_for("login_page"))
        except Exception:
            pass

        # 레거시 폴백: .env WEB_ADMIN_PASSWORD (DB 없을 때)
        expected_password = os.environ.get("WEB_ADMIN_PASSWORD", "")
        if not expected_password:
            flash("WEB_ADMIN_PASSWORD가 설정되지 않았습니다.", "error")
            return redirect(url_for("login_page"))
        if password != expected_password:
            flash("비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("login_page"))

        session.permanent = True
        session["user_id"] = 0
        session["username"] = "admin"
        session["is_admin"] = True
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
        return render_template("settings.html", values=values, masked_values=masked_values, managed_keys=MANAGED_KEYS, model_options=MODEL_OPTIONS)

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

    @app.get("/ask")
    @_login_required
    def ask_page():
        return render_template("ask.html")

    @app.post("/api/ask")
    @_login_required
    def api_ask():
        query = ""
        if request.is_json:
            query = str((request.get_json(silent=True) or {}).get("query", "")).strip()
        else:
            query = request.form.get("query", "").strip()
        if not query:
            return jsonify({"ok": False, "message": "질문을 입력해 주세요."}), 400
        try:
            result = execute_mode("ask", query=query, user_id=g.user_id)
            _append_action_history("ask", "success" if result["ok"] else "failed", query)
            return jsonify({"ok": result["ok"], "output": result.get("output", "")})
        except Exception as e:
            _append_action_history("ask", "failed", f"{query} | {e}")
            return jsonify({"ok": False, "message": str(e)}), 500

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
        user_id = session.get("user_id", 0)
        thread = threading.Thread(target=_run_ai_action_in_background, args=(mode, run_id, user_id), daemon=True)
        thread.start()
        return jsonify({"ok": True, "run_id": run_id, "mode": mode})

    @app.post("/actions/run")
    @_login_required
    def run_action():
        action = request.form.get("action", "").strip().lower()
        if action not in {"buy", "sell", "status"}:
            flash("지원하지 않는 액션입니다.", "error")
            return redirect(url_for("actions"))

        result = _run_bot_mode(action, user_id=g.user_id)
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

    @app.get("/api/ai-usage")
    @_login_required
    def api_ai_usage():
        return jsonify(_check_all_ai_usage())

    @app.get("/api/available-models")
    @_login_required
    def api_available_models():
        return jsonify(_fetch_all_available_models())

    @app.get("/schedule")
    @_login_required
    def schedule():
        try:
            snapshot = _load_schedule_snapshot()
        except Exception as e:
            flash(f"스케줄 조회 실패: {e}", "error")
            snapshot = {
                "supported": True,
                "enabled": False,
                "weekdays": "1-5",
                "timezone": "Asia/Seoul",
                "buy_times": [],
                "sell_times": [],
                "source": "error",
                "scheduler_state": {
                    "running": False,
                    "last_tick": "",
                    "last_message": "오류",
                    "last_triggered": {"buy": "", "sell": ""},
                },
                "now_in_timezone": "",
            }
        return render_template("schedule.html", schedule=snapshot)

    @app.post("/schedule/save")
    @_login_required
    def schedule_save():
        enabled = request.form.get("enabled", "") == "on"
        weekdays = request.form.get("weekdays", "1-5").strip()
        timezone_name = request.form.get("timezone", "Asia/Seoul").strip()
        buy_times_raw = request.form.get("buy_times", "").strip()
        sell_times_raw = request.form.get("sell_times", "").strip()
        try:
            buy_times = _normalize_hhmm_list(buy_times_raw)
            sell_times = _normalize_hhmm_list(sell_times_raw)
            if enabled and (not buy_times or not sell_times):
                raise ValueError("활성화 시 매수/매도 시간은 최소 1개 이상 필요합니다.")
            _save_schedule_config(
                enabled=enabled,
                weekdays=weekdays,
                timezone_name=timezone_name,
                buy_times=buy_times,
                sell_times=sell_times,
            )
            if enabled:
                flash("스케줄을 저장했습니다. 웹 스케줄러가 즉시 반영합니다.", "success")
            else:
                flash("자동 스케줄을 비활성화했습니다.", "success")
        except Exception as e:
            flash(f"스케줄 저장 실패: {e}", "error")
        return redirect(url_for("schedule"))

    @app.get("/prompts")
    @_login_required
    def prompts_page():
        from utils.prompt_manager import load_prompts, DEFAULT_BUY_TEMPLATE, DEFAULT_SELL_TEMPLATE, DEFAULT_BUDGET_TEMPLATE
        current = load_prompts()
        return render_template(
            "prompts.html",
            buy_template=current["buy"],
            sell_template=current["sell"],
            budget_template=current.get("budget", DEFAULT_BUDGET_TEMPLATE),
            default_buy=DEFAULT_BUY_TEMPLATE,
            default_sell=DEFAULT_SELL_TEMPLATE,
            default_budget=DEFAULT_BUDGET_TEMPLATE,
            prompts_path=str(PROMPTS_PATH),
        )

    @app.post("/prompts/save")
    @_login_required
    def prompts_save():
        from utils.prompt_manager import save_prompts
        buy_template = request.form.get("buy_template", "").strip()
        sell_template = request.form.get("sell_template", "").strip()
        budget_template = request.form.get("budget_template", "").strip()
        if not buy_template or not sell_template or not budget_template:
            flash("매수/매도/예산 프롬프트는 비워둘 수 없습니다.", "error")
            return redirect(url_for("prompts_page"))
        try:
            save_prompts(buy_template, sell_template, budget_template)
            flash("프롬프트가 저장되었습니다. 다음 AI 실행부터 반영됩니다.", "success")
        except Exception as e:
            flash(f"저장 실패: {e}", "error")
        return redirect(url_for("prompts_page"))

    @app.post("/prompts/reset")
    @_login_required
    def prompts_reset():
        from utils.prompt_manager import reset_prompts
        try:
            reset_prompts()
            flash("프롬프트를 기본값으로 초기화했습니다.", "success")
        except Exception as e:
            flash(f"초기화 실패: {e}", "error")
        return redirect(url_for("prompts_page"))

    # ── 관리자: 사용자 관리 ──────────────────────────

    @app.get("/admin/users")
    @_admin_required
    def admin_users():
        try:
            import db as db_module
            users = db_module.get_all_users()
        except Exception:
            users = []
        return render_template("admin_users.html", users=users)

    @app.post("/admin/users/create")
    @_admin_required
    def admin_create_user():
        import db as db_module
        from auth import hash_password
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        display_name = request.form.get("display_name", "").strip() or username
        is_admin = request.form.get("is_admin") == "on"

        if not username or not password:
            flash("사용자명과 비밀번호는 필수입니다.", "error")
            return redirect(url_for("admin_users"))

        try:
            db_module.create_user(username, hash_password(password), display_name, is_admin)
            flash(f"사용자 '{username}' 생성 완료.", "success")
        except Exception as e:
            flash(f"생성 실패: {e}", "error")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:uid>/toggle")
    @_admin_required
    def admin_toggle_user(uid: int):
        import db as db_module
        user = db_module.get_user_by_id(uid)
        if user:
            db_module.update_user(uid, is_active=0 if user["is_active"] else 1)
            flash(f"사용자 '{user['username']}' 상태 변경.", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:uid>/reset-password")
    @_admin_required
    def admin_reset_password(uid: int):
        import db as db_module
        from auth import hash_password
        new_pw = request.form.get("new_password", "").strip()
        if not new_pw:
            flash("새 비밀번호를 입력하세요.", "error")
            return redirect(url_for("admin_users"))
        db_module.update_user(uid, password_hash=hash_password(new_pw))
        flash("비밀번호가 변경되었습니다.", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:uid>/delete")
    @_admin_required
    def admin_delete_user(uid: int):
        import db as db_module
        if uid == g.user_id:
            flash("자신의 계정은 삭제할 수 없습니다.", "error")
            return redirect(url_for("admin_users"))
        db_module.delete_user(uid)
        flash("사용자가 삭제되었습니다.", "success")
        return redirect(url_for("admin_users"))

    # ── context processor: 모든 템플릿에 사용자 정보 주입 ──
    @app.context_processor
    def inject_user():
        return {
            "current_user_id": session.get("user_id", 0),
            "current_username": session.get("username", ""),
            "current_is_admin": session.get("is_admin", False),
        }

    return app


def run_web_admin(
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    debug: bool = False,
) -> None:
    """웹 관리자 서버를 실행합니다."""
    global _RUNTIME_LISTEN
    _RUNTIME_LISTEN = (host, port)
    load_dotenv(ENV_PATH, override=False)
    app = create_app()
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_web_admin()
