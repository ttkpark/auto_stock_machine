"""
주식 종목 검증기 (AI 환각 방지)

FinanceDataReader 를 사용하여 KRX 상장 종목 목록을 매일 갱신하고,
AI가 추천한 종목명이 실제 상장 종목인지 검증합니다.

pip install finance-datareader
"""
import logging
from typing import Optional
from datetime import date, timedelta

import requests
import FinanceDataReader as fdr

logger = logging.getLogger(__name__)


class StockValidator:
    def __init__(self):
        self._krx_dict: dict[str, str] = {}  # {종목명: 종목코드}
        self._code_set: set[str] = set()  # 전체 종목코드 집합 (검색 결과 검증용)
        self._last_updated: Optional[date] = None
        self.refresh()

    def refresh(self) -> None:
        """KRX 전체 상장 종목 + ETF 딕셔너리를 최신 데이터로 갱신합니다."""
        logger.info("[StockValidator] KRX 종목 목록 갱신 중...")
        try:
            krx = fdr.StockListing("KRX")
            self._krx_dict = {}
            self._code_set = set()

            # Name 외에 추가 이름 컬럼이 있으면 함께 색인
            name_columns = ["Name"]
            for col in krx.columns:
                if col != "Name" and any(
                    kw in col.upper() for kw in ["NAME", "NM", "ABBRV", "종목"]
                ):
                    name_columns.append(col)

            for _, row in krx.iterrows():
                code = str(row.get("Code", "")).strip()
                if not code:
                    continue
                self._code_set.add(code)
                for col in name_columns:
                    val = str(row.get(col, "")).strip()
                    if val and val != "nan":
                        self._krx_dict[val] = code

            # ETF 목록 추가 로드 (KRX 일반 종목에 ETF가 포함되지 않음)
            try:
                etf = fdr.StockListing("ETF/KR")
                etf_count = 0
                for _, row in etf.iterrows():
                    code = str(row.get("Symbol", "")).strip()
                    if not code:
                        continue
                    self._code_set.add(code)
                    name = str(row.get("Name", "")).strip()
                    if name and name != "nan":
                        self._krx_dict[name] = code
                        etf_count += 1
                logger.info(f"[StockValidator] ETF {etf_count}개 추가 로드 완료.")
            except Exception as e:
                logger.warning(f"[StockValidator] ETF 목록 로드 실패 (무시): {e}")

            self._last_updated = date.today()
            logger.info(
                f"[StockValidator] 총 {len(self._krx_dict)}개 항목 "
                f"({len(self._code_set)}개 종목) 로드 완료."
            )
        except Exception as e:
            logger.error(f"[StockValidator] KRX 종목 목록 갱신 실패: {e}")

    def _ensure_fresh(self) -> None:
        """마지막 갱신이 오늘이 아니면 자동 갱신합니다."""
        if self._last_updated != date.today():
            self.refresh()

    def _find_stock_code(self, obj) -> Optional[str]:
        """중첩 JSON 구조에서 6자리 종목코드를 재귀 탐색합니다."""
        if isinstance(obj, str):
            s = obj.strip()
            if len(s) == 6 and s.isdigit():
                return s
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_stock_code(item)
                if result:
                    return result
        return None

    def _search_by_name(self, query: str) -> Optional[str]:
        """
        네이버 금융 자동완성 API로 종목코드를 검색합니다.
        검색된 코드가 KRX 상장 종목인 경우에만 반환합니다. (환각 방지 유지)
        """
        try:
            resp = requests.get(
                "https://ac.finance.naver.com/ac",
                params={
                    "q": query,
                    "q_enc": "utf-8",
                    "t_koreng": "1",
                    "st": "111",
                    "r_lt": "111",
                },
                timeout=5,
            )
            data = resp.json()
            code = self._find_stock_code(data.get("items", []))
            if code and code in self._code_set:
                logger.info(
                    f"[StockValidator] '{query}' 네이버 검색 → 종목코드: {code}"
                )
                return code
        except Exception as e:
            logger.warning(f"[StockValidator] 네이버 금융 검색 실패: {e}")
        return None

    def verify_and_get_code(self, stock_name: str) -> Optional[str]:
        """
        AI가 추천한 종목명을 검증하고 종목코드를 반환합니다.

        - 정확한 이름으로 찾으면 코드 반환
        - 부분 일치로 찾으면 코드 반환 (예: "삼성전자우" -> "삼성전자" 검색)
        - 네이버 금융 검색으로 찾으면 코드 반환 (한글↔영문 이름 불일치 대응)
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

        # 3차: 네이버 금융 검색 (한글↔영문 이름 불일치 대응)
        code = self._search_by_name(name)
        if code:
            self._krx_dict[name] = code  # 캐시에 추가하여 재검색 방지
            return code

        logger.warning(
            f"[StockValidator] '{name}' 은 KRX 상장 종목이 아닙니다. (환각 차단)"
        )
        return None

    def get_all_tickers(self) -> dict[str, str]:
        """전체 종목 딕셔너리 반환 {종목명: 종목코드}"""
        self._ensure_fresh()
        return self._krx_dict.copy()
