"""
텔레그램 알림 모듈

python-telegram-bot 패키지를 사용합니다.
pip install python-telegram-bot
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if not self.token or not self.chat_id:
            logger.warning(
                ".env 파일에 TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID 가 "
                "모두 설정되어 있어야 합니다."
            )

    def send(self, message: str) -> bool:
        """텔레그램으로 메시지를 전송합니다."""
        if not self.token or not self.chat_id:
            logger.error("[Telegram] 토큰 또는 채팅 ID가 설정되지 않았습니다.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("[Telegram] 메시지 전송 성공.")
            return True
        except Exception as e:
            logger.error(f"[Telegram] 메시지 전송 실패: {e}")
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
