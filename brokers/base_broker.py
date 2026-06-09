"""
브로커 추상 기반 클래스 (인터페이스 정의)

C++의 순수 가상 클래스와 동일한 역할.
MockBroker, RealBroker 모두 이 규격을 반드시 구현해야 합니다.
"""
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 일시적 장애로 보고 재시도할 HTTP 상태코드
RETRY_STATUS_CODES = (500, 502, 503, 504, 429)


def request_with_retry(
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    timeout: int = 10,
    retry_statuses: tuple = RETRY_STATUS_CODES,
    label: str = "",
    **kwargs,
) -> requests.Response:
    """조회성 KIS API 호출용 재시도 래퍼.

    일시적 오류(5xx/429, 연결 오류, 타임아웃)에 한해 지수 백오프(1s→2s→4s)로
    최대 max_attempts회 재시도합니다. 4xx(429 제외) 등 영구 오류는 즉시 전파합니다.

    ⚠️ 주문(order-cash)처럼 비멱등(중복 실행이 위험한) 호출에는 사용하지 마세요.
        조회/토큰 발급 등 안전하게 반복 가능한 호출에만 사용합니다.

    반환: raise_for_status()를 통과한 requests.Response
    """
    last_exc: Optional[Exception] = None
    what = label or url
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            # 일시적 상태코드이고 재시도 여지가 있으면 백오프 후 재시도
            if resp.status_code in retry_statuses and attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"[KIS] {what} 일시적 오류 HTTP {resp.status_code} "
                    f"— {wait:.0f}s 후 재시도 ({attempt}/{max_attempts})"
                )
                time.sleep(wait)
                continue
            # 2xx면 통과, 그 외(영구 4xx·마지막 시도의 5xx)는 예외 발생
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"[KIS] {what} 연결오류({type(e).__name__}) "
                    f"— {wait:.0f}s 후 재시도 ({attempt}/{max_attempts})"
                )
                time.sleep(wait)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError(f"[KIS] {what} 재시도 실패")


class BaseBroker(ABC):
    """
    증권사 통신 인터페이스.
    메인 봇 로직은 이 추상 클래스만 알면 되며,
    실제 구현체(Mock/Real)가 무엇인지 알 필요 없습니다.
    """

    @abstractmethod
    def get_access_token(self) -> str:
        """API 인증 토큰 발급 및 반환"""
        pass

    @abstractmethod
    def get_balance(self) -> int:
        """주문 가능 예수금(원) 반환"""
        pass

    @abstractmethod
    def get_holdings(self) -> list[dict]:
        """
        보유 종목 목록 반환.
        반환 형식: [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "qty": 10,
                "avg_price": 75000,
                "current_price": 80000,
                "profit_rate": 6.67
            },
            ...
        ]
        """
        pass

    @abstractmethod
    def get_current_price(self, ticker: str) -> Optional[int]:
        """특정 종목의 현재가 반환 (원). 조회 실패 시 None 반환."""
        pass

    @abstractmethod
    def buy_order(self, ticker: str, qty: int) -> bool:
        """
        시장가 매수 주문.
        ticker: 종목코드 (예: "005930")
        qty: 매수 수량
        반환: 주문 성공 여부
        """
        pass

    @abstractmethod
    def sell_order(self, ticker: str, qty: int) -> bool:
        """
        시장가 매도 주문.
        ticker: 종목코드 (예: "005930")
        qty: 매도 수량
        반환: 주문 성공 여부
        """
        pass
