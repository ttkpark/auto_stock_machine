"""AI 프롬프트 템플릿 관리.

템플릿은 data/prompts.json 에 저장되며, 파일이 없으면 기본값을 사용합니다.
플레이스홀더는 {변수명} 형식이며, JSON 예시의 중괄호({" "})는 영향을 받지 않습니다.

매수 변수:  {balance}, {budget_instruction}, {market_info_line}
예산 변수:  {buy_budget_ratio}, {max_buy_stocks}
매도 변수:  {stock_name}, {ticker}, {qty}, {avg_price}, {current_price}, {profit_rate}
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
    "{market_info_line}\n\n"
    "이 예수금 한도 내에서 지금 매수하면 좋을 한국 주식을 딱 1개만 추천해 주세요.\n"
    "반드시 한국거래소(KRX)에 실제 상장된 정확한 종목명을 사용해야 합니다.\n"
    "답변은 반드시 아래 JSON 형식으로만 출력하세요. 다른 설명은 절대 하지 마세요.\n"
    '{"종목명": "삼성전자", "이유": "저평가 구간 진입"}'
)

DEFAULT_SELL_TEMPLATE = (
    "당신은 한국 주식 전문가입니다.\n"
    "내가 보유한 {stock_name}({ticker}) 주식은 {qty}주이고, "
    "평단가는 {avg_price}원인데 현재가는 {current_price}원 (수익률 {profit_rate}%)입니다.\n"
    "현재 시장 상황에서 지금 매도할까요, 보유할까요?\n"
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


def load_prompts() -> dict:
    if PROMPTS_PATH.exists():
        try:
            data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            return {
                "buy": data.get("buy", DEFAULT_BUY_TEMPLATE),
                "sell": data.get("sell", DEFAULT_SELL_TEMPLATE),
                "budget": data.get("budget", DEFAULT_BUDGET_TEMPLATE),
            }
        except Exception:
            pass
    return {
        "buy": DEFAULT_BUY_TEMPLATE,
        "sell": DEFAULT_SELL_TEMPLATE,
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


def build_budget_instruction() -> str:
    template = load_prompts()["budget"]
    ratio_raw = _safe_float_env("BUY_BUDGET_RATIO", 0.9)
    max_stocks = _safe_int_env("MAX_BUY_STOCKS", 3)
    return _apply_template(
        template,
        {
            "buy_budget_ratio": f"{ratio_raw * 100:.0f}",
            "max_buy_stocks": str(max_stocks),
        },
    )


def build_buy_prompt(balance: int, market_info: str = "") -> str:
    template = load_prompts()["buy"]
    market_info_line = f"추가 시장 정보: {market_info}" if market_info else ""
    return _apply_template(
        template,
        {
            "balance": f"{balance:,}",
            "budget_instruction": build_budget_instruction(),
            "market_info_line": market_info_line,
        },
    )


def build_sell_prompt(
    stock_name: str,
    ticker: str,
    qty: int,
    avg_price: int,
    current_price: int,
    profit_rate: float,
) -> str:
    template = load_prompts()["sell"]
    return _apply_template(
        template,
        {
            "stock_name": stock_name,
            "ticker": ticker,
            "qty": str(qty),
            "avg_price": f"{avg_price:,}",
            "current_price": f"{current_price:,}",
            "profit_rate": f"{profit_rate:.1f}",
        },
    )
