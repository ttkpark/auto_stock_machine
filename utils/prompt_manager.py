"""AI 프롬프트 템플릿 관리.

템플릿은 data/prompts.json 에 저장되며, 파일이 없으면 기본값을 사용합니다.
플레이스홀더는 {변수명} 형식이며, JSON 예시의 중괄호({" "})는 영향을 받지 않습니다.

매수 변수:  {balance}, {budget_instruction}, {market_info_line}
예산 변수:  {buy_budget_ratio}, {max_buy_stocks}
매도 변수:  {stock_name}, {ticker}, {qty}, {avg_price}, {current_price}, {profit_rate}, {market_info_line}
"""
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
PROMPTS_PATH = BASE_DIR / "data" / "prompts.json"

DEFAULT_BUDGET_TEMPLATE = (
    "예수금의 {buy_budget_ratio}%를 오늘 매수에 사용하며, "
    "최대 {max_buy_stocks}개 종목에 분산 투자합니다."
)

DEFAULT_BUY_TEMPLATE = (
    "당신은 한국 주식 전문가입니다.\n"
    "내 주문 가능 예수금은 {balance}원입니다.\n"
    "{budget_instruction}\n"
    "{budget_per_stock_info}"
    "{market_info_line}\n\n"
    "이 예수금 한도 내에서 지금 매수하면 좋을 한국 주식을 딱 1개만 추천해 주세요.\n"
    "반드시 한국거래소(KRX)에 실제 상장된 정확한 종목명을 사용해야 합니다.\n"
    "답변은 반드시 아래 JSON 형식으로만 출력하세요. 다른 설명은 절대 하지 마세요.\n"
    '{"종목명": "삼성전자", "이유": "저평가 구간 진입"}'
)

DEFAULT_ASK_TEMPLATE = (
    "당신은 한국 주식 전문가입니다.\n"
    "{stock_name}({ticker}) 종목에 대해 분석해 주세요.\n"
    "현재가: {current_price}원\n\n"
    "다음 항목을 포함하여 간결하게 답변해 주세요:\n"
    "1. 기업 개요 (한 줄)\n"
    "2. 최근 이슈 및 시장 동향\n"
    "3. 투자 매력도 (강점/리스크)\n"
    "4. 종합 의견 (매수 추천 / 관망 / 주의)\n\n"
    "답변은 반드시 아래 JSON 형식으로만 출력하세요. 다른 설명은 절대 하지 마세요.\n"
    '{{"기업개요": "...", "최근이슈": "...", "강점": "...", "리스크": "...", "종합의견": "매수 추천", "한줄요약": "..."}}'
)

DEFAULT_SELL_TEMPLATE = (
    "당신은 한국 주식 전문가입니다.\n"
    "내가 보유한 {stock_name}({ticker}) 주식은 {qty}주이고, "
    "평단가는 {avg_price}원인데 현재가는 {current_price}원 (수익률 {profit_rate}%)입니다.\n"
    "{market_info_line}\n"
    "위 정보를 종합적으로 고려하여 지금 매도할까요, 보유할까요?\n"
    "답변은 반드시 아래 JSON 형식으로만 출력하세요.\n"
    '{"결정": "매도", "이유": "목표 수익률 달성"}\n'
    '"결정" 필드는 반드시 "매도" 또는 "보유" 중 하나여야 합니다.'
)

# {영문_식별자} 패턴만 치환 — JSON 예시 {"키": ...} 는 건드리지 않음
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _apply_template(template: str, variables: dict) -> str:
    def replace(m: re.Match) -> str:
        key = m.group(1)
        return str(variables[key]) if key in variables else m.group(0)

    return _PLACEHOLDER_RE.sub(replace, template)


def _safe_float_env(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _safe_int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_prompts(user_id: int = 0) -> dict:
    """사용자별 프롬프트를 DB에서 로드합니다. DB에 없으면 기본값을 반환합니다."""
    if user_id:
        try:
            import db as _db
            row = _db.get_user_prompts(user_id)
            if row:
                return {
                    "buy": row.get("buy_template", "") or DEFAULT_BUY_TEMPLATE,
                    "sell": row.get("sell_template", "") or DEFAULT_SELL_TEMPLATE,
                    "ask": DEFAULT_ASK_TEMPLATE,
                    "budget": row.get("budget_template", "") or DEFAULT_BUDGET_TEMPLATE,
                }
        except Exception:
            pass
    # fallback: 파일 기반 (레거시) 또는 기본값
    if PROMPTS_PATH.exists():
        try:
            data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            return {
                "buy": data.get("buy", DEFAULT_BUY_TEMPLATE),
                "sell": data.get("sell", DEFAULT_SELL_TEMPLATE),
                "ask": data.get("ask", DEFAULT_ASK_TEMPLATE),
                "budget": data.get("budget", DEFAULT_BUDGET_TEMPLATE),
            }
        except Exception:
            pass
    return {
        "buy": DEFAULT_BUY_TEMPLATE,
        "sell": DEFAULT_SELL_TEMPLATE,
        "ask": DEFAULT_ASK_TEMPLATE,
        "budget": DEFAULT_BUDGET_TEMPLATE,
    }


def save_prompts(
    buy_template: str,
    sell_template: str,
    budget_template: str | None = None,
) -> None:
    current = load_prompts()
    PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_PATH.write_text(
        json.dumps(
            {
                "buy": buy_template,
                "sell": sell_template,
                "budget": budget_template if budget_template is not None else current["budget"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def reset_prompts() -> None:
    save_prompts(DEFAULT_BUY_TEMPLATE, DEFAULT_SELL_TEMPLATE, DEFAULT_BUDGET_TEMPLATE)


def build_budget_instruction(user_id: int = 0) -> str:
    template = load_prompts(user_id=user_id)["budget"]
    ratio_raw = _safe_float_env("BUY_BUDGET_RATIO", 0.9)
    max_stocks = _safe_int_env("MAX_BUY_STOCKS", 3)
    return _apply_template(
        template,
        {
            "buy_budget_ratio": f"{ratio_raw * 100:.0f}",
            "max_buy_stocks": str(max_stocks),
        },
    )


def build_buy_prompt(balance: int, market_info: str = "", budget_per_stock: int = 0, user_id: int = 0) -> str:
    template = load_prompts(user_id=user_id)["buy"]
    market_info_line = f"추가 시장 정보: {market_info}" if market_info else ""
    # budget_per_stock이 지정되면 AI에게 할당 예산 정보 제공
    budget_per_stock_info = ""
    if budget_per_stock > 0:
        budget_per_stock_info = f"각 종목당 할당 예산은 약 {budget_per_stock:,}원입니다.\n"
    return _apply_template(
        template,
        {
            "balance": f"{balance:,}",
            "budget_instruction": build_budget_instruction(user_id=user_id),
            "budget_per_stock_info": budget_per_stock_info,
            "market_info_line": market_info_line,
        },
    )


def build_ask_prompt(stock_name: str, ticker: str, current_price: int, user_id: int = 0) -> str:
    template = load_prompts(user_id=user_id).get("ask", DEFAULT_ASK_TEMPLATE)
    return _apply_template(
        template,
        {
            "stock_name": stock_name,
            "ticker": ticker,
            "current_price": f"{current_price:,}",
        },
    )


def build_sell_prompt(
    stock_name: str,
    ticker: str,
    qty: int,
    avg_price: int,
    current_price: int,
    profit_rate: float,
    market_info: str = "",
    user_id: int = 0,
) -> str:
    template = load_prompts(user_id=user_id)["sell"]
    market_info_line = market_info if market_info else ""
    return _apply_template(
        template,
        {
            "stock_name": stock_name,
            "ticker": ticker,
            "qty": str(qty),
            "avg_price": f"{avg_price:,}",
            "current_price": f"{current_price:,}",
            "profit_rate": f"{profit_rate:.1f}",
            "market_info_line": market_info_line,
        },
    )
