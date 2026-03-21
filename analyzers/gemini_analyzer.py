"""
Google Gemini AI 분석기

google-generativeai 패키지를 사용합니다.
pip install google-generativeai
"""
import os
import json
import logging
import re
from typing import Optional

import google.generativeai as genai

from .base_analyzer import BaseAnalyzer, BuyRecommendation, SellDecision

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "gemini-2.0-flash"
FALLBACK_MODEL_NAMES = ["gemini-2.5-flash", "gemini-flash-latest"]


class GeminiAnalyzer(BaseAnalyzer):
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                ".env 파일에 GEMINI_API_KEY 가 설정되어 있지 않습니다."
            )
        genai.configure(api_key=api_key)
        preferred_model = os.environ.get("GEMINI_MODEL_NAME", "").strip() or DEFAULT_MODEL_NAME
        self.model_name = preferred_model
        self.model = genai.GenerativeModel(preferred_model)
        self.last_recommendation_error = ""
        logger.info(f"[GeminiAnalyzer] 초기화 완료 (모델: {self.model_name})")

    def _generate_content_with_fallback(self, prompt: str):
        model_candidates = [self.model_name] + [
            m for m in FALLBACK_MODEL_NAMES if m != self.model_name
        ]
        last_error = None
        for idx, model_name in enumerate(model_candidates):
            try:
                if idx > 0:
                    self.model_name = model_name
                    self.model = genai.GenerativeModel(model_name)
                    logger.warning(f"[GeminiAnalyzer] 모델 폴백 적용: {model_name}")
                return self.model.generate_content(prompt)
            except Exception as e:
                last_error = e
                logger.error(f"[GeminiAnalyzer] 모델 호출 실패 ({model_name}): {e}")
        raise RuntimeError(f"Gemini 모델 호출 실패: {last_error}")

    def recommend_buy(self, balance: int, market_info: str = "") -> Optional[BuyRecommendation]:
        self.last_recommendation_error = ""
        prompt = (
            f"당신은 한국 주식 전문가입니다.\n"
            f"내 주문 가능 예수금은 {balance:,}원입니다.\n"
            f"{f'추가 시장 정보: {market_info}' if market_info else ''}\n\n"
            f"이 예수금 한도 내에서 지금 매수하면 좋을 한국 주식을 딱 1개만 추천해 주세요.\n"
            f"반드시 한국거래소(KRX)에 실제 상장된 정확한 종목명을 사용해야 합니다.\n"
            f"답변은 반드시 아래 JSON 형식으로만 출력하세요. 다른 설명은 절대 하지 마세요.\n"
            f'{{"종목명": "삼성전자", "이유": "저평가 구간 진입"}}'
        )
        try:
            response = self._generate_content_with_fallback(prompt)
            raw_text = response.text.strip()
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[GeminiAnalyzer] JSON 파싱 실패: {raw_text}")
                self.last_recommendation_error = f"JSON 파싱 실패: {raw_text[:180]}"
                return None

            if not parsed.get("종목명", "").strip():
                self.last_recommendation_error = "JSON에 종목명이 비어 있습니다."
                return None

            return BuyRecommendation(
                stock_name=parsed.get("종목명", ""),
                reason=parsed.get("이유", ""),
                ai_model="Gemini",
            )
        except Exception as e:
            logger.error(f"[GeminiAnalyzer] 매수 추천 오류: {e}")
            self.last_recommendation_error = f"API 오류: {e}"
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
        prompt = (
            f"당신은 한국 주식 전문가입니다.\n"
            f"내가 보유한 {stock_name}({ticker}) 주식은 {qty}주이고, "
            f"평단가는 {avg_price:,}원인데 현재가는 {current_price:,}원 (수익률 {profit_rate:.1f}%)입니다.\n"
            f"현재 시장 상황에서 지금 매도할까요, 보유할까요?\n"
            f"답변은 반드시 아래 JSON 형식으로만 출력하세요.\n"
            f'{{"결정": "매도", "이유": "목표 수익률 달성"}}'
            f'\n"결정" 필드는 반드시 "매도" 또는 "보유" 중 하나여야 합니다.'
        )
        try:
            response = self._generate_content_with_fallback(prompt)
            raw_text = response.text.strip()
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[GeminiAnalyzer] 매도 판단 JSON 파싱 실패: {raw_text}")
                return SellDecision(action="보유", reason="파싱 오류 - 안전을 위해 보유", ai_model="Gemini")

            return SellDecision(
                action=parsed.get("결정", "보유"),
                reason=parsed.get("이유", ""),
                ai_model="Gemini",
            )
        except Exception as e:
            logger.error(f"[GeminiAnalyzer] 매도 판단 오류: {e}")
            return SellDecision(action="보유", reason=f"API 오류: {e}", ai_model="Gemini")

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
