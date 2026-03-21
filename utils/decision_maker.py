"""
다수결 교차 검증 의사결정 모듈

여러 AI 분석기의 결과를 취합하여 교집합 종목을 선정하거나
매도 여부를 다수결로 판단합니다.
"""
import logging
from collections import Counter
from typing import Optional

from analyzers.base_analyzer import BuyRecommendation, SellDecision

logger = logging.getLogger(__name__)


class DecisionMaker:
    def __init__(self, min_consensus: int = 2):
        """
        min_consensus: 매수 결정을 위해 최소 몇 개의 AI가 동의해야 하는지.
                       기본값 2 (3개 중 2개 이상 동의 시 매수).
        """
        self.min_consensus = min_consensus

    def find_buy_consensus(
        self, recommendations: list[Optional[BuyRecommendation]]
    ) -> Optional[str]:
        """
        AI 추천 목록에서 다수결로 공통 종목을 선정합니다.

        반환: 합의된 종목명 또는 합의 실패 시 None
        """
        valid = [r for r in recommendations if r is not None and r.stock_name]
        if not valid:
            logger.info("[DecisionMaker] 유효한 매수 추천이 없습니다.")
            return None

        name_counter = Counter(r.stock_name for r in valid)
        logger.info(f"[DecisionMaker] AI 추천 집계: {dict(name_counter)}")

        top_stock, top_count = name_counter.most_common(1)[0]
        if top_count >= self.min_consensus:
            ai_names = [r.ai_model for r in valid if r.stock_name == top_stock]
            logger.info(
                f"[DecisionMaker] 합의 종목 선정: '{top_stock}' "
                f"({top_count}/{len(valid)} 동의: {ai_names})"
            )
            return top_stock

        logger.info(
            f"[DecisionMaker] 합의 실패. 최다 추천: '{top_stock}' ({top_count}표) "
            f"- 최소 {self.min_consensus}표 필요."
        )
        return None

    def decide_sell_by_vote(self, decisions: list[SellDecision]) -> SellDecision:
        """
        AI 판단 목록에서 다수결로 매도/보유를 결정합니다.

        반환: 최종 SellDecision
        """
        if not decisions:
            return SellDecision(action="보유", reason="판단 결과 없음", ai_model="System")

        action_counter = Counter(d.action for d in decisions)
        sell_count = action_counter.get("매도", 0)
        hold_count = action_counter.get("보유", 0)

        logger.info(f"[DecisionMaker] 매도 판단 집계: 매도={sell_count}, 보유={hold_count}")

        if sell_count > hold_count:
            reasons = [d.reason for d in decisions if d.action == "매도"]
            return SellDecision(
                action="매도",
                reason=" / ".join(reasons),
                ai_model="Consensus",
            )
        else:
            reasons = [d.reason for d in decisions if d.action == "보유"]
            return SellDecision(
                action="보유",
                reason=" / ".join(reasons) if reasons else "과반 보유 의견",
                ai_model="Consensus",
            )
