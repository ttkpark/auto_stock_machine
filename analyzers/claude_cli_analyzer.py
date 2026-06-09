"""
Anthropic Claude CLI 분석기 (API 키 없이 동작)

`anthropic` API 패키지 대신 로컬에 설치된 `claude` CLI(Claude Code)를 headless
모드(`claude -p`)로 호출합니다. 원격 서버에서 `claude login`(구독 로그인)을 한 번
해두면 API 키 없이 매수/매도 판단을 API처럼 사용할 수 있습니다.

설치(우분투 예시):
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
    npm install -g @anthropic-ai/claude-code
    claude login        # 봇을 실행하는 동일한 OS 사용자로 로그인

환경 변수:
    CLAUDE_CLI_ENABLED   "true"/"false" (기본 true) — 분석기 등록 여부
    CLAUDE_CLI_MODEL     CLI 모델 별칭 (기본 "sonnet"; "haiku"/"opus" 등)
    CLAUDE_CLI_PATH      claude 실행 파일 경로 (기본 "claude")
    CLAUDE_CLI_TIMEOUT   호출 타임아웃 초 (기본 120)
"""
import os
import json
import shutil
import logging
import re
import subprocess
import tempfile
from typing import Optional

from .base_analyzer import BaseAnalyzer, BuyRecommendation, SellDecision, StockAnalysis
from utils.prompt_manager import (
    CLI_SYSTEM_PROMPT,
    build_buy_prompt_cli,
    build_sell_prompt_cli,
    build_ask_prompt_cli,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "sonnet"
DEFAULT_TIMEOUT = 120

# headless 호출에서 도구 사용을 막아 결정 함수처럼 텍스트만 생성하도록 제한
_DISALLOWED_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Task"


class ClaudeCliAnalyzer(BaseAnalyzer):
    def __init__(
        self,
        model_name: str | None = None,
        cli_path: str | None = None,
        timeout: int | None = None,
    ):
        path = cli_path if cli_path is not None else os.environ.get("CLAUDE_CLI_PATH", "")
        self.cli_path = (path.strip() or "claude")
        resolved = shutil.which(self.cli_path)
        if not resolved:
            raise EnvironmentError(
                f"claude CLI를 찾을 수 없습니다 (경로: {self.cli_path}). "
                "Node.js 설치 후 'npm install -g @anthropic-ai/claude-code' 및 "
                "'claude login'을 먼저 수행하세요."
            )
        self.cli_path = resolved

        model = model_name if model_name is not None else os.environ.get("CLAUDE_CLI_MODEL", "")
        self.model_name = model.strip() or DEFAULT_MODEL_NAME

        timeout_env = os.environ.get("CLAUDE_CLI_TIMEOUT", "").strip()
        if timeout is not None:
            self.timeout = timeout
        elif timeout_env.isdigit():
            self.timeout = int(timeout_env)
        else:
            self.timeout = DEFAULT_TIMEOUT

        self.last_recommendation_error = ""
        logger.info(f"[ClaudeCliAnalyzer] 초기화 완료 (모델: {self.model_name}, CLI: {self.cli_path})")

    def _run(self, prompt: str) -> str:
        """claude CLI를 headless로 호출하고 모델 응답 텍스트를 반환합니다.

        - 임시 빈 디렉터리를 cwd로 사용해 프로젝트 CLAUDE.md 자동 탐색을 막습니다.
        - 프롬프트는 stdin으로 전달해 인자 길이/이스케이프 문제를 피합니다.
        - --output-format json 으로 받아 result 필드를 추출합니다.
        """
        cmd = [
            self.cli_path,
            "-p",
            "--output-format", "json",
            "--model", self.model_name,
            "--disallowed-tools", _DISALLOWED_TOOLS,
            "--append-system-prompt", CLI_SYSTEM_PROMPT,
        ]
        workdir = tempfile.mkdtemp(prefix="claude_cli_")
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=workdir,
                timeout=self.timeout,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 비정상 종료(code={proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
            )

        stdout = (proc.stdout or "").strip()
        if not stdout:
            raise RuntimeError(f"claude CLI 빈 응답 (stderr: {(proc.stderr or '').strip()[:200]})")

        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            # --output-format json 이 아닌 형태로 오면 본문을 그대로 사용
            return stdout

        if envelope.get("is_error"):
            raise RuntimeError(
                f"claude CLI 오류 응답: {str(envelope.get('result', ''))[:300]}"
            )
        result = envelope.get("result", "")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(f"claude CLI result 비어 있음: {stdout[:200]}")
        return result.strip()

    def recommend_buy(
        self,
        balance: int,
        market_info: str = "",
        budget_per_stock: int = 0,
        user_id: int = 0,
    ) -> Optional[BuyRecommendation]:
        self.last_recommendation_error = ""
        prompt = build_buy_prompt_cli(
            balance=balance,
            market_info=market_info,
            budget_per_stock=budget_per_stock,
            user_id=user_id,
        )
        try:
            raw_text = self._run(prompt)
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[ClaudeCliAnalyzer] JSON 파싱 실패: {raw_text}")
                self.last_recommendation_error = f"JSON 파싱 실패: {raw_text[:180]}"
                return None

            if not parsed.get("종목명", "").strip():
                self.last_recommendation_error = "JSON에 종목명이 비어 있습니다."
                return None

            return BuyRecommendation(
                stock_name=parsed.get("종목명", ""),
                reason=parsed.get("이유", ""),
                ai_model="Claude(CLI)",
            )
        except Exception as e:
            logger.error(f"[ClaudeCliAnalyzer] 매수 추천 오류: {e}")
            self.last_recommendation_error = f"CLI 오류: {e}"
            return None

    def analyze_stock(self, stock_name: str, ticker: str, current_price: int) -> Optional[StockAnalysis]:
        prompt = build_ask_prompt_cli(stock_name=stock_name, ticker=ticker, current_price=current_price)
        try:
            raw_text = self._run(prompt)
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[ClaudeCliAnalyzer] 종목 분석 JSON 파싱 실패: {raw_text}")
                return None
            return StockAnalysis(
                summary=parsed.get("기업개요", ""),
                recent_issues=parsed.get("최근이슈", ""),
                strengths=parsed.get("강점", ""),
                risks=parsed.get("리스크", ""),
                opinion=parsed.get("종합의견", ""),
                one_liner=parsed.get("한줄요약", ""),
                ai_model="Claude(CLI)",
            )
        except Exception as e:
            logger.error(f"[ClaudeCliAnalyzer] 종목 분석 오류: {e}")
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
        prompt = build_sell_prompt_cli(
            stock_name=stock_name,
            ticker=ticker,
            qty=qty,
            avg_price=avg_price,
            current_price=current_price,
            profit_rate=profit_rate,
            market_info=market_info,
        )
        try:
            raw_text = self._run(prompt)
            parsed = self._parse_json(raw_text)
            if not parsed:
                logger.warning(f"[ClaudeCliAnalyzer] 매도 판단 JSON 파싱 실패: {raw_text}")
                return SellDecision(action="보유", reason="파싱 오류 - 안전을 위해 보유", ai_model="Claude(CLI)", is_error=True)

            return SellDecision(
                action=parsed.get("결정", "보유"),
                reason=parsed.get("이유", ""),
                ai_model="Claude(CLI)",
            )
        except Exception as e:
            logger.error(f"[ClaudeCliAnalyzer] 매도 판단 오류: {e}")
            return SellDecision(action="보유", reason=f"CLI 오류: {e}", ai_model="Claude(CLI)", is_error=True)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """AI 응답에서 JSON 부분만 추출하여 파싱 (코드펜스 포함 처리)."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                snippet = match.group()
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    # 가장 바깥 객체가 실패하면 최소 객체로 재시도
                    inner = re.search(r"\{.*?\}", text, re.DOTALL)
                    if inner:
                        try:
                            return json.loads(inner.group())
                        except json.JSONDecodeError:
                            pass
        return None
