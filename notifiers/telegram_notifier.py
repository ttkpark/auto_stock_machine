"""
텔레그램 알림 모듈

python-telegram-bot 패키지를 사용합니다.
pip install python-telegram-bot
"""
import os
import json
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


class TelegramNotifier:
    SUBSCRIBERS_FILE = Path("data/telegram_subscribers.json")
    UPDATES_OFFSET_FILE = Path("data/telegram_updates_offset.txt")

    def __init__(self, token: str | None = None, chat_id: str | None = None, user_id: int = 0):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.user_id = user_id

        if not self.token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN 이 설정되어 있어야 합니다."
            )
            return

        # 런타임 구독자 저장 경로 준비
        self.SUBSCRIBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._sync_subscribers_from_updates()

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _load_subscribers(self) -> set[str]:
        # DB 모드 (user_id > 0)
        if self.user_id > 0:
            try:
                import db as db_module
                return set(db_module.get_telegram_subscribers(self.user_id))
            except Exception:
                pass

        # 레거시: JSON 파일
        if not self.SUBSCRIBERS_FILE.exists():
            return set()
        try:
            data = json.loads(self.SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
        except Exception as e:
            logger.warning(f"[Telegram] 구독자 목록 로드 실패: {e}")
        return set()

    def _save_subscribers(self, subscribers: set[str]) -> None:
        # DB 모드 (user_id > 0)
        if self.user_id > 0:
            try:
                import db as db_module
                for chat_id in subscribers:
                    db_module.add_telegram_subscriber(self.user_id, chat_id)
                return
            except Exception:
                pass

        # 레거시: JSON 파일
        def _subscriber_sort_key(chat_id: str):
            normalized = chat_id.strip()
            signed = normalized[1:] if normalized.startswith("-") else normalized
            if signed.isdigit():
                return (0, int(normalized))
            return (1, normalized)

        sorted_list = sorted(subscribers, key=_subscriber_sort_key)
        self.SUBSCRIBERS_FILE.write_text(
            json.dumps(sorted_list, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_update_offset(self) -> int:
        if not self.UPDATES_OFFSET_FILE.exists():
            return 0
        try:
            return int(self.UPDATES_OFFSET_FILE.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            return 0

    def _save_update_offset(self, offset: int) -> None:
        self.UPDATES_OFFSET_FILE.write_text(str(offset), encoding="utf-8")

    def _send_to_chat(self, chat_id: str, message: str) -> bool:
        url = self._api_url("sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[Telegram] 메시지 전송 실패(chat_id={chat_id}): {e}")
            return False

    def _sync_subscribers_from_updates(self) -> None:
        """
        봇으로 들어온 메시지를 읽어 구독자를 갱신합니다.
        - start: 구독 등록
        - end: 구독 해제
        """
        if not self.token:
            return

        subscribers = self._load_subscribers()
        offset = self._load_update_offset()
        params = {"timeout": 0}
        if offset > 0:
            params["offset"] = offset

        try:
            resp = requests.get(self._api_url("getUpdates"), params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Telegram] getUpdates 호출 실패: {e}")
            return

        updates = data.get("result", [])
        if not updates:
            # 초기 TELEGRAM_CHAT_ID 는 fallback 수신자로 저장
            if self.chat_id and self.chat_id not in subscribers:
                subscribers.add(self.chat_id)
                self._save_subscribers(subscribers)
            return

        max_update_id = offset
        for update in updates:
            update_id = int(update.get("update_id", 0))
            max_update_id = max(max_update_id, update_id + 1)

            message = update.get("message") or {}
            chat = message.get("chat") or {}
            text = (message.get("text") or "").strip().lower()
            chat_id = str(chat.get("id", "")).strip()
            if not chat_id:
                continue

            if text == "start":
                subscribers.add(chat_id)
                self._send_to_chat(
                    chat_id,
                    "알림 구독이 등록되었습니다. 이제 매매/현황 보고를 받습니다.",
                )
                logger.info(f"[Telegram] 구독 등록: {chat_id}")
            elif text == "end":
                subscribers.discard(chat_id)
                self._send_to_chat(
                    chat_id,
                    "알림 구독이 해제되었습니다. 더 이상 보고를 보내지 않습니다.",
                )
                logger.info(f"[Telegram] 구독 해제: {chat_id}")

        if self.chat_id and self.chat_id not in subscribers:
            subscribers.add(self.chat_id)

        self._save_subscribers(subscribers)
        self._save_update_offset(max_update_id)

    def send(self, message: str) -> bool:
        """텔레그램으로 메시지를 전송합니다."""
        if not self.token:
            logger.error("[Telegram] 토큰이 설정되지 않았습니다.")
            return False

        # 메시지 전송 직전에 최신 구독 상태를 한 번 더 반영
        self._sync_subscribers_from_updates()
        subscribers = self._load_subscribers()
        if not subscribers and self.chat_id:
            subscribers.add(self.chat_id)

        if not subscribers:
            logger.error("[Telegram] 수신자(chat_id)가 없습니다. start를 먼저 보내 주세요.")
            return False

        success_count = 0
        for chat_id in subscribers:
            if self._send_to_chat(chat_id, message):
                success_count += 1

        if success_count > 0:
            logger.info(f"[Telegram] 메시지 전송 성공: {success_count}명")
            return True
        return False

    def notify_buy_order(self, stock_name: str, ticker: str, qty: int, price: int) -> None:
        msg = (
            f"<b>[매수 체결]</b>\n"
            f"종목: {stock_name} ({ticker})\n"
            f"수량: {qty}주\n"
            f"현재가: {price:,}원\n"
            f"총 금액: {qty * price:,}원"
        )
        self.send(msg)

    def notify_sell_order(
        self, stock_name: str, ticker: str, qty: int, avg_price: int, sell_price: int
    ) -> None:
        profit = (sell_price - avg_price) * qty
        profit_rate = (sell_price - avg_price) / avg_price * 100
        profit_sign = "+" if profit >= 0 else ""
        msg = (
            f"<b>[매도 체결]</b>\n"
            f"종목: {stock_name} ({ticker})\n"
            f"수량: {qty}주\n"
            f"평단가: {avg_price:,}원 → 매도가: {sell_price:,}원\n"
            f"손익: {profit_sign}{profit:,}원 ({profit_sign}{profit_rate:.1f}%)"
        )
        self.send(msg)

    def notify_error(self, context: str, error: Exception) -> None:
        msg = (
            f"<b>[시스템 오류]</b>\n"
            f"위치: {context}\n"
            f"내용: {str(error)}"
        )
        self.send(msg)

    def notify_daily_summary(self, balance: int, holdings: list[dict]) -> None:
        lines = [f"<b>[일일 현황 보고]</b>", f"예수금: {balance:,}원\n"]
        if holdings:
            lines.append("<b>보유 종목:</b>")
            for h in holdings:
                sign = "+" if h["profit_rate"] >= 0 else ""
                lines.append(
                    f"  {h['name']} ({h['ticker']}) "
                    f"{h['qty']}주 | "
                    f"{sign}{h['profit_rate']:.1f}%"
                )
        else:
            lines.append("보유 종목 없음")
        self.send("\n".join(lines))
