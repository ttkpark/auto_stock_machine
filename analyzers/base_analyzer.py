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
class StockAnalysis:
    """종목 분석 결과 데이터 구조"""
    summary: str               # 기업 개요
    recent_issues: str         # 최근 이슈
    strengths: str             # 강점
    risks: str                 # 리스크
    opinion: str               # 종합 의견
    one_liner: str             # 한줄 요약
    ai_model: str              # 분석한 AI 모델명


@dataclass
class SellDecision:
    """매도/보유 판단 결과 데이터 구조"""
    action: str            # "매도" 또는 "보유"
    reason: str            # 판단 이유
    ai_model: str          # 판단한 AI 모델명
    is_error: bool = False # API 오류 등으로 정상 판단이 아닌 경우 True


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

    def analyze_stock(
        self,
        stock_name: str,
        ticker: str,
        current_price: int,
    ) -> Optional[StockAnalysis]:
        """
        특정 종목에 대한 AI 분석.
        기본 구현은 None을 반환합니다.
        """
        return None

    @abstractmethod
    def decide_sell(
        self,
        stock_name: str,
        ticker: str,
        qty: int,
        avg_price: int,
        current_price: int,
        profit_rate: float,
        market_info: str = "",
    ) -> SellDecision:
        """
        보유 종목의 매도 여부 판단.
        market_info: 기술 지표·시장 현황 등 추가 컨텍스트 (선택)
        반환: SellDecision (action = "매도" 또는 "보유")
        """
        pass
