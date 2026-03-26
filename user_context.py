"""사용자 컨텍스트 — 사용자별 의존성 팩토리.

각 사용자의 설정/브로커/분석기/알림기를 하나로 묶어 제공합니다.
execute_mode()에서 이 객체를 생성하여 전체 실행 체인에 주입합니다.
"""
import logging
import os
from dataclasses import dataclass, field

import db as db_module
from db import MANAGED_USER_KEYS

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_id: int
    username: str
    config: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 팩토리 메서드
    # ------------------------------------------------------------------

    @classmethod
    def from_user_id(cls, user_id: int) -> "UserContext":
        """DB에서 사용자 정보와 설정을 로드합니다."""
        user = db_module.get_user_by_id(user_id)
        if not user:
            raise ValueError(f"사용자 ID {user_id}를 찾을 수 없습니다.")
        config = db_module.get_user_config(user_id)
        return cls(user_id=user_id, username=user["username"], config=config)

    @classmethod
    def from_env_fallback(cls) -> "UserContext":
        """CLI용: .env에서 직접 읽어 단일 사용자 모드로 동작합니다."""
        config = {}
        for key in MANAGED_USER_KEYS:
            val = os.environ.get(key, "")
            if val:
                config[key] = val
        return cls(user_id=0, username="admin", config=config)

    # ------------------------------------------------------------------
    # 의존성 생성
    # ------------------------------------------------------------------

    def get_broker(self):
        """사용자 설정에 따라 MockBroker 또는 RealBroker를 반환합니다."""
        is_real = self.config.get("IS_REAL_TRADING", "False").lower() == "true"
        if is_real:
            from brokers import RealBroker
            return RealBroker(
                app_key=self.config.get("KIS_REAL_APP_KEY", ""),
                app_secret=self.config.get("KIS_REAL_APP_SECRET", ""),
                account_number=self.config.get("KIS_REAL_ACCOUNT_NUMBER", ""),
                user_id=self.user_id,
            )
        else:
            from brokers import MockBroker
            return MockBroker(
                app_key=self.config.get("KIS_MOCK_APP_KEY", ""),
                app_secret=self.config.get("KIS_MOCK_APP_SECRET", ""),
                account_number=self.config.get("KIS_MOCK_ACCOUNT_NUMBER", ""),
                user_id=self.user_id,
            )

    def get_analyzers(self) -> list:
        """활성화된 AI 분석기 목록을 반환합니다."""
        analyzers = []

        gemini_key = self.config.get("GEMINI_API_KEY", "")
        if gemini_key:
            try:
                from analyzers import GeminiAnalyzer
                analyzers.append(GeminiAnalyzer(
                    api_key=gemini_key,
                    model_name=self.config.get("GEMINI_MODEL_NAME", ""),
                ))
            except Exception as e:
                logger.warning(f"GeminiAnalyzer 초기화 실패: {e}")

        claude_key = self.config.get("CLAUDE_API_KEY", "")
        if claude_key:
            try:
                from analyzers import ClaudeAnalyzer
                analyzers.append(ClaudeAnalyzer(
                    api_key=claude_key,
                    model_name=self.config.get("CLAUDE_MODEL_NAME", ""),
                ))
            except Exception as e:
                logger.warning(f"ClaudeAnalyzer 초기화 실패: {e}")

        openai_key = self.config.get("OPENAI_API_KEY", "")
        if openai_key:
            try:
                from analyzers import OpenAIAnalyzer
                analyzers.append(OpenAIAnalyzer(
                    api_key=openai_key,
                    model_name=self.config.get("OPENAI_MODEL_NAME", ""),
                ))
            except Exception as e:
                logger.warning(f"OpenAIAnalyzer 초기화 실패: {e}")

        if not analyzers:
            raise RuntimeError(
                "활성화된 AI 분석기가 없습니다. "
                "설정에서 GEMINI_API_KEY, CLAUDE_API_KEY, OPENAI_API_KEY 중 하나 이상을 설정해 주세요."
            )
        return analyzers

    def get_notifier(self):
        """사용자 설정에 따른 TelegramNotifier를 반환합니다."""
        from notifiers import TelegramNotifier
        return TelegramNotifier(
            token=self.config.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=self.config.get("TELEGRAM_CHAT_ID", ""),
            user_id=self.user_id,
        )

    def get_holdings_tracker(self):
        """사용자별 HoldingsTracker를 반환합니다."""
        from utils import HoldingsTracker
        return HoldingsTracker(user_id=self.user_id)

    def get_config_value(self, key: str, default: str = "") -> str:
        return self.config.get(key, default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.config.get(key, ""))
        except (ValueError, TypeError):
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.config.get(key, ""))
        except (ValueError, TypeError):
            return default
