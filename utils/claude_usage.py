"""claude CLI 호출량 모니터링 (폭주 감지 안전장치).

스케줄러가 스스로 실행하는 작업(매수/매도)은 '되어져야 하는' claude CLI 호출 수가
대체로 정해져 있습니다.
  - 매수: claude 분석기 수
  - 매도: 보유종목 수 × claude 분석기 수 (종목별·분석기별 1회가 정상 상한)

무한 루프·재시도 폭주·로직 버그 등으로 실제 호출이 이 예상치의 일정 배수
(기본 2배, CLAUDE_CALL_ALERT_MULTIPLIER 로 조정)를 초과하면 텔레그램으로 1회 경고합니다.

사용:
  from utils.claude_usage import monitor
  monitor.begin_run(expected_calls=N, notifier=notifier, label="매수")  # 실행 시작 시
  # ... 이후 ClaudeCliAnalyzer._run() 이 자동으로 monitor.record_call() 호출 ...
"""
import os
import logging
import threading

logger = logging.getLogger(__name__)


def _default_multiplier() -> float:
    raw = (os.environ.get("CLAUDE_CALL_ALERT_MULTIPLIER", "") or "").strip()
    try:
        val = float(raw)
        return val if val > 0 else 2.0
    except ValueError:
        return 2.0


class _ClaudeUsageMonitor:
    """모듈 전역 싱글턴. 한 번에 하나의 실행(run)을 추적합니다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0
        self._budget = 0
        self._multiplier = _default_multiplier()
        self._notifier = None
        self._alerted = False
        self._label = ""

    def begin_run(self, expected_calls, notifier=None, label="", multiplier=None):
        """새 실행 시작: 카운터를 초기화하고 예상 호출 수(budget)를 설정합니다."""
        with self._lock:
            self._count = 0
            self._budget = max(0, int(expected_calls or 0))
            self._notifier = notifier
            self._alerted = False
            self._label = label or ""
            self._multiplier = float(multiplier) if multiplier else _default_multiplier()
        logger.info(
            f"[claude_usage] 모니터 시작 | {self._label} | "
            f"예상 호출 {self._budget}회 (경고 임계 {self._budget * self._multiplier:g}회)"
        )

    def record_call(self):
        """claude CLI 호출 1회를 기록합니다. 임계 초과 시 텔레그램 경고를 1회 전송."""
        notifier = None
        message = None
        with self._lock:
            self._count += 1
            count = self._count
            budget = self._budget
            multiplier = self._multiplier
            threshold = budget * multiplier
            if budget > 0 and not self._alerted and count > threshold:
                self._alerted = True
                notifier = self._notifier
                message = (
                    f"⚠️ <b>claude 호출량 경고</b>\n"
                    f"작업: {self._label or '(미지정)'}\n"
                    f"예상 호출 {budget}회의 {multiplier:g}배({threshold:g}회)를 초과했습니다.\n"
                    f"현재 누적 claude CLI 호출: <b>{count}회</b>\n"
                    f"무한 루프/재시도 폭주 등 비정상 동작 가능성을 점검하세요."
                )
        if message:
            logger.warning(
                f"[claude_usage] 호출량 임계 초과 | {self._label} | "
                f"누적 {count}회 > 임계 {threshold:g}회 (예상 {budget}회)"
            )
        if notifier and message:
            try:
                notifier.send(message)
            except Exception as e:
                logger.error(f"[claude_usage] 텔레그램 경고 전송 실패: {e}")

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


# 모듈 전역 싱글턴
monitor = _ClaudeUsageMonitor()
