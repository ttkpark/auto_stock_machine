"""
OpenAI ChatGPT AI 분석기

openai 패키지를 사용합니다.
pip install openai
"""
import os
import json
import logging
import re
from typing import Optional

from openai import OpenAI

from .base_analyzer import BaseAnalyzer, BuyRecommendation, SellDecision, StockAnalysis
from utils.prompt_manager import build_buy_prompt, build_sell_prompt, build_ask_prompt

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "gpt-4o-mini"


class OpenAIAnalyzer(BaseAnalyzer):
    def __init__(self, api_key: str = "", model_name: str = ""):
        api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "OPENAI_API_KEY 가 설정되어 있지 않습니다."
            )
        self.client = OpenAI(api_key=api_key)
        self.model_name = (model_name or os.environ.get("OPENAI_MODEL_NAME", "")).strip() or DEFAULT_MODEL_NAME
        self.last_recommendation_error = ""
        logger.info(f"[OpenAIAnalyzer] 초기화 완료 (모델: {self.model_name})")

    def _chat(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        return response.choices[0].message.content.strip()

    def recommend_buy(self, balance: int, market_info: str = "") -> Optional[BuyRecommendation]:
        self.last_recommendation_error = ""
        prompt = build_buy_prompt(balance=balance, market_info=market_info)
        try:
            raw_text = self._chat(prompt)
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[OpenAIAnalyzer] JSON 파싱 실패: {raw_text}")
                self.last_recommendation_error = f"JSON 파싱 실패: {raw_text[:180]}"
                return None

            if not parsed.get("종목명", "").strip():
                self.last_recommendation_error = "JSON에 종목명이 비어 있습니다."
                return None

            return BuyRecommendation(
                stock_name=parsed.get("종목명", ""),
                reason=parsed.get("이유", ""),
                ai_model="ChatGPT",
            )
        except Exception as e:
            logger.error(f"[OpenAIAnalyzer] 매수 추천 오류: {e}")
            self.last_recommendation_error = f"API 오류: {e}"
            return None

    def analyze_stock(self, stock_name: str, ticker: str, current_price: int) -> Optional[StockAnalysis]:
        prompt = build_ask_prompt(stock_name=stock_name, ticker=ticker, current_price=current_price)
        try:
            raw_text = self._chat(prompt)
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[OpenAIAnalyzer] 종목 분석 JSON 파싱 실패: {raw_text}")
                return None
            return StockAnalysis(
                summary=parsed.get("기업개요", ""),
                recent_issues=parsed.get("최근이슈", ""),
                strengths=parsed.get("강점", ""),
                risks=parsed.get("리스크", ""),
                opinion=parsed.get("종합의견", ""),
                one_liner=parsed.get("한줄요약", ""),
                ai_model="ChatGPT",
            )
        except Exception as e:
            logger.error(f"[OpenAIAnalyzer] 종목 분석 오류: {e}")
            return None

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
        prompt = build_sell_prompt(
            stock_name=stock_name,
            ticker=ticker,
            qty=qty,
            avg_price=avg_price,
            current_price=current_price,
            profit_rate=profit_rate,
            market_info=market_info,
        )
        try:
            raw_text = self._chat(prompt)
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[OpenAIAnalyzer] 매도 판단 JSON 파싱 실패: {raw_text}")
                return SellDecision(action="보유", reason="파싱 오류 - 안전을 위해 보유", ai_model="ChatGPT", is_error=True)

            return SellDecision(
                action=parsed.get("결정", "보유"),
                reason=parsed.get("이유", ""),
                ai_model="ChatGPT",
            )
        except Exception as e:
            logger.error(f"[OpenAIAnalyzer] 매도 판단 오류: {e}")
            return SellDecision(action="보유", reason=f"API 오류: {e}", ai_model="ChatGPT", is_error=True)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*?\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None
