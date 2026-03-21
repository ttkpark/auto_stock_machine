"""
브로커 추상 기반 클래스 (인터페이스 정의)

C++의 순수 가상 클래스와 동일한 역할.
MockBroker, RealBroker 모두 이 규격을 반드시 구현해야 합니다.
"""
from abc import ABC, abstractmethod
from typing import Optional


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
