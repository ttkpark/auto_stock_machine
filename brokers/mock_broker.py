"""
모의투자 브로커

한국투자증권 모의투자 API와 통신합니다.
URL: https://openapivts.koreainvestment.com:29443
실제 돈이 움직이지 않으므로, 개발 및 테스트 단계에서 사용하세요.
"""
import os
import json
import time
import requests
import logging
from pathlib import Path
from typing import Optional
from .base_broker import BaseBroker

logger = logging.getLogger(__name__)


class MockBroker(BaseBroker):
    BASE_URL = "https://openapivts.koreainvestment.com:29443"

    def __init__(self, app_key: str | None = None, app_secret: str | None = None,
                 account_number: str | None = None, user_id: int = 0):
        self.app_key = app_key if app_key is not None else os.environ.get("KIS_MOCK_APP_KEY", "")
        self.app_secret = app_secret if app_secret is not None else os.environ.get("KIS_MOCK_APP_SECRET", "")
        self.account_number = account_number if account_number is not None else os.environ.get("KIS_MOCK_ACCOUNT_NUMBER", "")
        self.user_id = user_id
        self._access_token: Optional[str] = None
        self.last_order_error: str = ""

        if not all([self.app_key, self.app_secret, self.account_number]):
            logger.warning(
                "KIS_MOCK_APP_KEY, KIS_MOCK_APP_SECRET, "
                "KIS_MOCK_ACCOUNT_NUMBER 가 모두 설정되어 있는지 확인하세요."
            )
        logger.info("[MockBroker] 모의투자 브로커 초기화 완료.")

    # ------------------------------------------------------------------ #
    #  인증 토큰
    # ------------------------------------------------------------------ #
    def _load_cached_token(self) -> Optional[str]:
        # DB 기반 토큰 캐시 (user_id > 0)
        if self.user_id > 0:
            try:
                import db as db_module
                cached = db_module.get_cached_token(self.user_id, "mock")
                if cached:
                    return cached["access_token"]
            except Exception:
                pass
            return None

        # 레거시: JSON 파일 캐시
        token_file = Path("data/kis_mock_token.json")
        if not token_file.exists():
            return None
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
            token = str(data.get("access_token", "")).strip()
            expires_at = int(data.get("expires_at", 0))
            if token and expires_at > int(time.time()) + 30:
                return token
        except Exception:
            return None
        return None

    def _save_cached_token(self, token: str, expires_in: int) -> None:
        safe_expires_at = int(time.time()) + max(0, int(expires_in) - 60)

        # DB 기반 토큰 캐시 (user_id > 0)
        if self.user_id > 0:
            try:
                import db as db_module
                db_module.save_cached_token(self.user_id, "mock", token, safe_expires_at)
                return
            except Exception:
                pass

        # 레거시: JSON 파일 캐시
        token_file = Path("data/kis_mock_token.json")
        token_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"access_token": token, "expires_at": safe_expires_at}
        token_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        cached = self._load_cached_token()
        if cached:
            self._access_token = cached
            return self._access_token

        url = f"{self.BASE_URL}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._save_cached_token(self._access_token, int(data.get("expires_in", 21600)))
        logger.info("[MockBroker] 액세스 토큰 발급 성공.")
        return self._access_token

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

    def _validate_response(self, data: dict, api_name: str) -> bool:
        """
        KIS 응답 공통 검증.
        정상(rt_cd == "0")이 아니거나 에러 구조일 때 원인 로그를 남깁니다.
        """
        rt_cd = str(data.get("rt_cd", ""))
        if rt_cd and rt_cd != "0":
            logger.error(
                f"[MockBroker] {api_name} 실패 (rt_cd={rt_cd}, msg_cd={data.get('msg_cd')}, msg1={data.get('msg1')})"
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    #  계좌 정보
    # ------------------------------------------------------------------ #
    def get_balance(self) -> int:
        # inquire-psbl-order의 ord_psbl_cash(주문가능현금)를 사용합니다.
        # ※ inquire-balance의 dnca_tot_amt(예탁금총금액)는 순수 현금 입금액만
        #   표시하므로 정산 대기금·CMA 평가액이 빠져 실제 매수 가능 금액보다
        #   훨씬 적게 나옵니다. (예: 실제 1,028만원인데 17만원으로 표시)
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        headers = self._headers()
        headers["tr_id"] = "VTTC8908R"  # 모의투자 주문 가능 금액 조회

        account_prefix = self.account_number[:8]
        account_suffix = self.account_number[8:]

        params = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "PDNO": "005930",
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "Y",
            "OVRS_ICLD_YN": "N",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not self._validate_response(data, "주문 가능 금액 조회"):
            return 0

        output = data.get("output")
        if not isinstance(output, dict):
            logger.error(f"[MockBroker] 주문 가능 금액 조회 output 누락 | raw={data}")
            return 0

        balance = int(float(output.get("ord_psbl_cash", 0)))
        logger.info(f"[MockBroker] 주문가능현금: {balance:,}원")
        return balance

    def get_holdings(self) -> list[dict]:
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = self._headers()
        headers["tr_id"] = "VTTC8434R"  # 모의투자 잔고 조회

        account_prefix = self.account_number[:8]
        account_suffix = self.account_number[8:]

        params = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not self._validate_response(data, "잔고 조회"):
            return []

        holdings = []
        for item in data.get("output1", []):
            qty = int(float(item.get("hldg_qty", 0)))
            if qty <= 0:
                continue
            holdings.append({
                "ticker": item.get("pdno", ""),
                "name": item.get("prdt_name", ""),
                "qty": qty,
                "avg_price": int(float(item.get("pchs_avg_pric", 0))),
                "current_price": int(float(item.get("prpr", 0))),
                "profit_rate": float(item.get("evlu_pfls_rt", 0.0)),
            })

        # output2에서 총 평가 금액을 인스턴스에 저장 (대시보드용)
        output2 = data.get("output2", [])
        if output2 and isinstance(output2, list) and output2[0]:
            self.tot_evlu_amt = int(float(output2[0].get("tot_evlu_amt", 0)))
        else:
            self.tot_evlu_amt = 0

        return holdings

    def get_current_price(self, ticker: str) -> Optional[int]:
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = self._headers()
        headers["tr_id"] = "FHKST01010100"

        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not self._validate_response(data, "현재가 조회"):
                return None

            output = data.get("output")
            if not isinstance(output, dict) or output.get("stck_prpr") is None:
                logger.error(f"[MockBroker] 현재가 조회 응답 형식 오류 ({ticker}) | raw={data}")
                return None

            price = int(float(output["stck_prpr"]))
            return price
        except Exception as e:
            logger.error(f"[MockBroker] 현재가 조회 실패 ({ticker}): {e}")
            return None

    # ------------------------------------------------------------------ #
    #  주문
    # ------------------------------------------------------------------ #
    def buy_order(self, ticker: str, qty: int) -> bool:
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = self._headers()
        headers["tr_id"] = "VTTC0802U"  # 모의투자 시장가 매수

        account_prefix = self.account_number[:8]
        account_suffix = self.account_number[8:]

        payload = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "PDNO": ticker,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result.get("rt_cd") == "0":
                self.last_order_error = ""
                logger.info(f"[MockBroker] 매수 성공: {ticker} {qty}주")
                return True
            else:
                self.last_order_error = str(result.get("msg1", "원인 미상"))
                logger.error(f"[MockBroker] 매수 실패: {self.last_order_error}")
                return False
        except Exception as e:
            self.last_order_error = str(e)
            logger.error(f"[MockBroker] 매수 주문 오류 ({ticker}): {e}")
            return False

    def sell_order(self, ticker: str, qty: int) -> bool:
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = self._headers()
        headers["tr_id"] = "VTTC0801U"  # 모의투자 시장가 매도

        account_prefix = self.account_number[:8]
        account_suffix = self.account_number[8:]

        payload = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "PDNO": ticker,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result.get("rt_cd") == "0":
                logger.info(f"[MockBroker] 매도 성공: {ticker} {qty}주")
                return True
            else:
                logger.error(f"[MockBroker] 매도 실패: {result.get('msg1')}")
                return False
        except Exception as e:
            logger.error(f"[MockBroker] 매도 주문 오류 ({ticker}): {e}")
            return False
