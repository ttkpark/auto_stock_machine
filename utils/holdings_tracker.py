"""보유 종목 매수일 및 트레일링 하이 추적기.

user_id > 0이면 DB(ai_traces), 아니면 data/holdings_tracker.json 사용.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import config as _cfg

logger = logging.getLogger(__name__)

TRACKER_PATH = Path("data/holdings_tracker.json")


class HoldingsTracker:
    """보유 종목의 매수일 및 트레일링 하이를 관리합니다."""

    def __init__(self, user_id: int = 0):
        self.user_id = user_id
        if user_id > 0:
            self._data = self._load_from_db()
        else:
            self._data = self._load_from_file()

    # ------------------------------------------------------------------
    # 데이터 로드
    # ------------------------------------------------------------------

    def _load_from_db(self) -> dict:
        try:
            import db as db_module
            return db_module.get_holdings_tracker_data(self.user_id)
        except Exception as e:
            logger.warning(f"DB holdings_tracker 로드 실패, 빈 데이터 사용: {e}")
            return {}

    def _load_from_file(self) -> dict:
        if TRACKER_PATH.exists():
            try:
                return json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"holdings_tracker.json 로드 실패, 초기화: {e}")
        return {}

    # ------------------------------------------------------------------
    # 데이터 저장
    # ------------------------------------------------------------------

    def _save_to_db(self, ticker: str | None = None) -> None:
        try:
            import db as db_module
            if ticker and ticker in self._data:
                entry = self._data[ticker]
                db_module.upsert_holding(
                    self.user_id, ticker,
                    entry.get("buy_date", _cfg.now().strftime("%Y-%m-%d")),
                    entry.get("trailing_high", 0),
                )
        except Exception as e:
            logger.warning(f"DB holdings_tracker 저장 실패: {e}")

    def _save_to_file(self) -> None:
        TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRACKER_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save(self, ticker: str | None = None) -> None:
        if self.user_id > 0:
            self._save_to_db(ticker)
        else:
            self._save_to_file()

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def record_buy(self, ticker: str, buy_date: str | None = None) -> None:
        """매수 기록. 이미 추적 중인 종목은 덮어쓰지 않습니다."""
        if ticker in self._data:
            return
        self._data[ticker] = {
            "buy_date": buy_date or _cfg.now().strftime("%Y-%m-%d"),
            "trailing_high": 0,
        }
        self._save(ticker)

    def record_sell(self, ticker: str) -> None:
        """매도 완료 시 추적 데이터 제거."""
        if ticker in self._data:
            del self._data[ticker]
            if self.user_id > 0:
                try:
                    import db as db_module
                    db_module.delete_holding(self.user_id, ticker)
                except Exception:
                    pass
            else:
                self._save_to_file()

    def update_trailing_high(self, ticker: str, current_price: int) -> None:
        """현재가가 기존 최고가보다 높으면 갱신."""
        if ticker not in self._data:
            return
        if current_price > self._data[ticker].get("trailing_high", 0):
            self._data[ticker]["trailing_high"] = current_price
            if self.user_id > 0:
                try:
                    import db as db_module
                    db_module.update_trailing_high(self.user_id, ticker, current_price)
                except Exception:
                    pass
            else:
                self._save_to_file()

    def get_holding_days(self, ticker: str) -> int | None:
        """매수일로부터 경과 일수. 추적 데이터 없으면 None."""
        entry = self._data.get(ticker)
        if not entry or "buy_date" not in entry:
            return None
        try:
            buy = datetime.strptime(entry["buy_date"], "%Y-%m-%d").date()
            return (_cfg.now().date() - buy).days
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
        """브로커 보유 목록과 동기화."""
        current_tickers = {h["ticker"] for h in holdings}

        # 없는 종목 제거
        removed = [t for t in self._data if t not in current_tickers]
        for t in removed:
            del self._data[t]
            if self.user_id > 0:
                try:
                    import db as db_module
                    db_module.delete_holding(self.user_id, t)
                except Exception:
                    pass

        # 새 종목 추가
        for h in holdings:
            ticker = h["ticker"]
            if ticker not in self._data:
                self._data[ticker] = {
                    "buy_date": _cfg.now().strftime("%Y-%m-%d"),
                    "trailing_high": h.get("current_price", 0),
                }
                self._save(ticker)

        if not self.user_id > 0:
            self._save_to_file()
