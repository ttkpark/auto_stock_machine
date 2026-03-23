"""보유 종목 매수일 및 트레일링 하이 추적기.

data/holdings_tracker.json에 종목별 매수일과 최고가를 기록하여
보유 기간 계산 및 동적 손절(trailing stop)에 활용합니다.
"""
import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TRACKER_PATH = Path("data/holdings_tracker.json")


class HoldingsTracker:
    """보유 종목의 매수일 및 트레일링 하이를 관리합니다."""

    def __init__(self):
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if TRACKER_PATH.exists():
            try:
                return json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"holdings_tracker.json 로드 실패, 초기화: {e}")
        return {}

    def _save(self) -> None:
        TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRACKER_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record_buy(self, ticker: str, buy_date: str | None = None) -> None:
        """매수 기록. 이미 추적 중인 종목은 덮어쓰지 않습니다."""
        if ticker in self._data:
            return
        self._data[ticker] = {
            "buy_date": buy_date or date.today().isoformat(),
            "trailing_high": 0,
        }
        self._save()

    def record_sell(self, ticker: str) -> None:
        """매도 완료 시 추적 데이터 제거."""
        if ticker in self._data:
            del self._data[ticker]
            self._save()

    def update_trailing_high(self, ticker: str, current_price: int) -> None:
        """현재가가 기존 최고가보다 높으면 갱신."""
        if ticker not in self._data:
            return
        if current_price > self._data[ticker].get("trailing_high", 0):
            self._data[ticker]["trailing_high"] = current_price
            self._save()

    def get_holding_days(self, ticker: str) -> int | None:
        """매수일로부터 경과 일수. 추적 데이터 없으면 None."""
        entry = self._data.get(ticker)
        if not entry or "buy_date" not in entry:
            return None
        try:
            buy = datetime.strptime(entry["buy_date"], "%Y-%m-%d").date()
            return (date.today() - buy).days
        except (ValueError, TypeError):
            return None

    def get_trailing_high(self, ticker: str) -> int | None:
        """추적 중인 최고가. 없거나 0이면 None."""
        entry = self._data.get(ticker)
        if not entry:
            return None
        val = entry.get("trailing_high", 0)
        return val if val > 0 else None

    def sync_from_holdings(self, holdings: list[dict]) -> None:
        """브로커 보유 목록과 동기화.

        - 보유 목록에 있지만 tracker에 없는 종목 → 오늘 날짜로 추가
        - tracker에 있지만 보유 목록에 없는 종목 → 제거
        """
        current_tickers = {h["ticker"] for h in holdings}

        # 없는 종목 제거
        removed = [t for t in self._data if t not in current_tickers]
        for t in removed:
            del self._data[t]

        # 새 종목 추가
        for h in holdings:
            ticker = h["ticker"]
            if ticker not in self._data:
                self._data[ticker] = {
                    "buy_date": date.today().isoformat(),
                    "trailing_high": h.get("current_price", 0),
                }

        if removed or any(h["ticker"] not in self._data for h in holdings):
            self._save()
        else:
            self._save()
