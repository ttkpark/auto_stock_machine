"""
Anthropic Claude AI 분석기

anthropic 패키지를 사용합니다.
pip install anthropic
"""
import os
import json
import logging
import re
from typing import Optional

import anthropic

from .base_analyzer import BaseAnalyzer, BuyRecommendation, SellDecision, StockAnalysis
from utils.prompt_manager import build_buy_prompt, build_sell_prompt, build_ask_prompt

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "claude-haiku-4-5-latest"
FALLBACK_MODEL_NAMES = [
    "claude-haiku-4-5-latest",
    "claude-sonnet-4-5-latest",
    "claude-sonnet-4-6-latest",
]


class ClaudeAnalyzer(BaseAnalyzer):
    def __init__(self):
        api_key = os.environ.get("CLAUDE_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                ".env 파일에 CLAUDE_API_KEY 가 설정되어 있지 않습니다."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model_name = os.environ.get("CLAUDE_MODEL_NAME", "").strip() or DEFAULT_MODEL_NAME
        self.last_recommendation_error = ""
        logger.info(f"[ClaudeAnalyzer] 초기화 완료 (모델: {self.model_name})")

    def _create_message_with_fallback(self, prompt: str):
        model_candidates = [self.model_name] + [m for m in FALLBACK_MODEL_NAMES if m != self.model_name]
        last_error = None
        for idx, model_name in enumerate(model_candidates):
            try:
                if idx > 0:
                    self.model_name = model_name
                    logger.warning(f"[ClaudeAnalyzer] 모델 폴백 적용: {model_name}")
                return self.client.messages.create(
                    model=model_name,
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as e:
                last_error = e
                logger.error(f"[ClaudeAnalyzer] 모델 호출 실패 ({model_name}): {e}")
        raise RuntimeError(f"Claude 모델 호출 실패: {last_error}")

    def recommend_buy(self, balance: int, market_info: str = "") -> Optional[BuyRecommendation]:
        self.last_recommendation_error = ""
        prompt = build_buy_prompt(balance=balance, market_info=market_info)
        try:
            message = self._create_message_with_fallback(prompt)
            raw_text = message.content[0].text.strip()
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[ClaudeAnalyzer] JSON 파싱 실패: {raw_text}")
                self.last_recommendation_error = f"JSON 파싱 실패: {raw_text[:180]}"
                return None

            if not parsed.get("종목명", "").strip():
                self.last_recommendation_error = "JSON에 종목명이 비어 있습니다."
                return None

            return BuyRecommendation(
                stock_name=parsed.get("종목명", ""),
                reason=parsed.get("이유", ""),
                ai_model="Claude",
            )
        except Exception as e:
            logger.error(f"[ClaudeAnalyzer] 매수 추천 오류: {e}")
            self.last_recommendation_error = f"API 오류: {e}"
            return None

    def analyze_stock(self, stock_name: str, ticker: str, current_price: int) -> Optional[StockAnalysis]:
        prompt = build_ask_prompt(stock_name=stock_name, ticker=ticker, current_price=current_price)
        try:
            message = self._create_message_with_fallback(prompt)
            raw_text = message.content[0].text.strip()
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[ClaudeAnalyzer] 종목 분석 JSON 파싱 실패: {raw_text}")
                return None
            return StockAnalysis(
                summary=parsed.get("기업개요", ""),
                recent_issues=parsed.get("최근이슈", ""),
                strengths=parsed.get("강점", ""),
                risks=parsed.get("리스크", ""),
                opinion=parsed.get("종합의견", ""),
                one_liner=parsed.get("한줄요약", ""),
                ai_model="Claude",
            )
        except Exception as e:
            logger.error(f"[ClaudeAnalyzer] 종목 분석 오류: {e}")
            return None

    def decide_sell(
        self,
        stock_name: str,
        ticker: str,
        qty: int,
        avg_price: int,
        current_price: int,
        profit_rate: float,
    ) -> SellDecision:
        prompt = build_sell_prompt(
            stock_name=stock_name,
            ticker=ticker,
            qty=qty,
            avg_price=avg_price,
            current_price=current_price,
            profit_rate=profit_rate,
        )
        try:
            message = self._create_message_with_fallback(prompt)
            raw_text = message.content[0].text.strip()
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[ClaudeAnalyzer] 매도 판단 JSON 파싱 실패: {raw_text}")
                return SellDecision(action="보유", reason="파싱 오류 - 안전을 위해 보유", ai_model="Claude")

            return SellDecision(
                action=parsed.get("결정", "보유"),
                reason=parsed.get("이유", ""),
                ai_model="Claude",
            )
        except Exception as e:
            logger.error(f"[ClaudeAnalyzer] 매도 판단 오류: {e}")
            return SellDecision(action="보유", reason=f"API 오류: {e}", ai_model="Claude")

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """AI 응답에서 JSON 부분만 추출하여 파싱"""
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
