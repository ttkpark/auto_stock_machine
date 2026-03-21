"""
AI 분석기 추상 기반 클래스

Gemini, Claude 등 모든 AI 분석기가 동일한 인터페이스를 가지도록 규격을 정의합니다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class BuyRecommendation:
    """매수 추천 결과 데이터 구조"""
    stock_name: str        # 종목명 (예: "삼성전자")
    reason: str            # 추천 이유
    ai_model: str          # 추천한 AI 모델명


@dataclass
class SellDecision:
    """매도/보유 판단 결과 데이터 구조"""
    action: str            # "매도" 또는 "보유"
    reason: str            # 판단 이유
    ai_model: str          # 판단한 AI 모델명


class BaseAnalyzer(ABC):
    """AI 분석기 인터페이스"""

    @abstractmethod
    def recommend_buy(self, balance: int, market_info: str = "") -> Optional[BuyRecommendation]:
        """
        매수 종목 추천.
        balance: 사용 가능한 예수금(원)
        market_info: 추가 시장 정보 (선택사항)
        반환: BuyRecommendation 또는 추천 불가 시 None
        """
        pass

    @abstractmethod
    def decide_sell(
        self,
        stock_name: str,
        ticker: str,
        qty: int,
        avg_price: int,
        current_price: int,
        profit_rate: float,
    ) -> SellDecision:
        """
        보유 종목의 매도 여부 판단.
        반환: SellDecision (action = "매도" 또는 "보유")
        """
        pass
