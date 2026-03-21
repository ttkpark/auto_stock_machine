"""
주식 종목 검증기 (AI 환각 방지)

FinanceDataReader 를 사용하여 KRX 상장 종목 목록을 매일 갱신하고,
AI가 추천한 종목명이 실제 상장 종목인지 검증합니다.

pip install finance-datareader
"""
import logging
from typing import Optional
from datetime import date, timedelta

import FinanceDataReader as fdr

logger = logging.getLogger(__name__)


class StockValidator:
    def __init__(self):
        self._krx_dict: dict[str, str] = {}  # {종목명: 종목코드}
        self._last_updated: Optional[date] = None
        self.refresh()

    def refresh(self) -> None:
        """KRX 전체 상장 종목 딕셔너리를 최신 데이터로 갱신합니다."""
        logger.info("[StockValidator] KRX 종목 목록 갱신 중...")
        try:
            krx = fdr.StockListing("KRX")
            self._krx_dict = {
                row["Name"].strip(): row["Code"]
                for _, row in krx.iterrows()
                if row.get("Name") and row.get("Code")
            }
            self._last_updated = date.today()
            logger.info(f"[StockValidator] 총 {len(self._krx_dict)}개 종목 로드 완료.")
        except Exception as e:
            logger.error(f"[StockValidator] KRX 종목 목록 갱신 실패: {e}")

    def _ensure_fresh(self) -> None:
        """마지막 갱신이 오늘이 아니면 자동 갱신합니다."""
        if self._last_updated != date.today():
            self.refresh()

    def verify_and_get_code(self, stock_name: str) -> Optional[str]:
        """
        AI가 추천한 종목명을 검증하고 종목코드를 반환합니다.

        - 정확한 이름으로 찾으면 코드 반환
        - 부분 일치로 찾으면 코드 반환 (예: "삼성전자우" -> "삼성전자" 검색)
        - 존재하지 않으면 None 반환 (환각 차단)
        """
        self._ensure_fresh()
        name = stock_name.strip()

        # 1차: 정확한 이름 매칭
        if name in self._krx_dict:
            code = self._krx_dict[name]
            logger.info(f"[StockValidator] '{name}' 검증 통과 → 종목코드: {code}")
            return code

        # 2차: 부분 일치 (AI가 약칭을 쓴 경우 대응)
        matches = [k for k in self._krx_dict if name in k or k in name]
        if len(matches) == 1:
            code = self._krx_dict[matches[0]]
            logger.info(
                f"[StockValidator] '{name}' 부분 일치 → '{matches[0]}' ({code})"
            )
            return code
        elif len(matches) > 1:
            logger.warning(
                f"[StockValidator] '{name}' 다중 매칭 ({matches[:5]}...) → 안전을 위해 거부"
            )
            return None

        logger.warning(f"[StockValidator] '{name}' 은 KRX 상장 종목이 아닙니다. (환각 차단)")
        return None

    def get_all_tickers(self) -> dict[str, str]:
        """전체 종목 딕셔너리 반환 {종목명: 종목코드}"""
        self._ensure_fresh()
        return self._krx_dict.copy()
