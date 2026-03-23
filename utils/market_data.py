"""시장 데이터 조회 및 기술 지표 계산.

FinanceDataReader를 사용하여 일봉 OHLCV를 가져오고,
RSI, MACD, Bollinger Bands, ATR 등 기술 지표를 계산합니다.
"""
import logging
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


class MarketDataProvider:
    """시장 데이터 조회 및 기술 지표 계산기."""

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        self._index_cache: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # 데이터 조회
    # ------------------------------------------------------------------

    def get_daily_prices(self, ticker: str, days: int | None = None) -> pd.DataFrame | None:
        """일봉 OHLCV 조회. 실패 시 None 반환."""
        days = days or self.lookback_days
        try:
            import FinanceDataReader as fdr

            end = datetime.now()
            start = end - timedelta(days=days)
            df = fdr.DataReader(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            logger.warning(f"일봉 조회 실패 ({ticker}): {e}")
            return None

    def get_market_index_change(self) -> dict | None:
        """KOSPI/KOSDAQ 전일 대비 등락률 및 5일 추세 반환."""
        try:
            import FinanceDataReader as fdr

            result = {}
            for code, name in [("KS11", "kospi"), ("KQ11", "kosdaq")]:
                if code in self._index_cache:
                    df = self._index_cache[code]
                else:
                    end = datetime.now()
                    start = end - timedelta(days=14)
                    df = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
                    self._index_cache[code] = df

                if df is None or len(df) < 2:
                    return None

                today_close = df["Close"].iloc[-1]
                prev_close = df["Close"].iloc[-2]
                change_pct = (today_close - prev_close) / prev_close * 100

                # 5일 추세
                if len(df) >= 6:
                    close_5d_ago = df["Close"].iloc[-6]
                    trend_pct = (today_close - close_5d_ago) / close_5d_ago * 100
                    if trend_pct > 1:
                        trend = "상승"
                    elif trend_pct < -1:
                        trend = "하락"
                    else:
                        trend = "보합"
                else:
                    trend = "알수없음"

                result[f"{name}_change_pct"] = round(change_pct, 2)
                result[f"{name}_5d_trend"] = trend

            return result
        except Exception as e:
            logger.warning(f"시장 지수 조회 실패: {e}")
            return None

    def is_market_crash(self, threshold: float = -2.0) -> bool:
        """KOSPI 또는 KOSDAQ이 threshold% 이상 하락했으면 True."""
        index_data = self.get_market_index_change()
        if not index_data:
            return False
        return (
            index_data.get("kospi_change_pct", 0) <= threshold
            or index_data.get("kosdaq_change_pct", 0) <= threshold
        )

    # ------------------------------------------------------------------
    # 기술 지표 계산 (순수 pandas)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_rsi(closes: pd.Series, period: int = 14) -> float | None:
        """RSI(Relative Strength Index) 계산. 0~100 범위."""
        if len(closes) < period + 1:
            return None
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        last_loss = loss.iloc[-1]
        if last_loss == 0:
            return 100.0
        rs = gain.iloc[-1] / last_loss
        return round(100 - (100 / (1 + rs)), 1)

    @staticmethod
    def compute_macd(
        closes: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> dict | None:
        """MACD 라인, 시그널, 히스토그램 반환."""
        if len(closes) < slow + signal:
            return None
        ema_fast = closes.ewm(span=fast, adjust=False).mean()
        ema_slow = closes.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return {
            "macd": round(float(macd_line.iloc[-1]), 2),
            "signal": round(float(signal_line.iloc[-1]), 2),
            "histogram": round(float(histogram.iloc[-1]), 2),
        }

    @staticmethod
    def compute_bollinger(closes: pd.Series, period: int = 20, num_std: float = 2.0) -> dict | None:
        """볼린저 밴드 상단/중단/하단 및 밴드 내 위치(0~1) 반환."""
        if len(closes) < period:
            return None
        middle = closes.rolling(period).mean()
        std = closes.rolling(period).std()
        upper = middle + num_std * std
        lower = middle - num_std * std

        cur = float(closes.iloc[-1])
        u = float(upper.iloc[-1])
        l = float(lower.iloc[-1])
        band_width = u - l
        position = (cur - l) / band_width if band_width > 0 else 0.5

        return {
            "upper": round(u, 0),
            "middle": round(float(middle.iloc[-1]), 0),
            "lower": round(l, 0),
            "position": round(position, 2),
        }

    @staticmethod
    def compute_atr(df: pd.DataFrame, period: int = 14) -> float | None:
        """ATR(Average True Range) 계산."""
        if df is None or len(df) < period + 1:
            return None
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.rolling(period).mean()
        val = atr.iloc[-1]
        if pd.isna(val):
            return None
        return round(float(val), 2)

    # ------------------------------------------------------------------
    # 통합 컨텍스트 빌드
    # ------------------------------------------------------------------

    def build_enriched_context(
        self,
        ticker: str,
        holding_days: int | None = None,
        trailing_high: int | None = None,
        atr_multiplier: float = 2.0,
    ) -> str:
        """기술 지표 + 시장 현황을 한국어 텍스트로 조합. 실패 시 빈 문자열."""
        lines: list[str] = []

        df = self.get_daily_prices(ticker)
        if df is not None and not df.empty:
            closes = df["Close"]
            current_price = float(closes.iloc[-1])

            # RSI
            rsi = self.compute_rsi(closes)
            if rsi is not None:
                if rsi >= 70:
                    rsi_label = "과매수 구간"
                elif rsi <= 30:
                    rsi_label = "과매도 구간"
                else:
                    rsi_label = "중립"
                rsi_text = f"RSI(14): {rsi} ({rsi_label})"
            else:
                rsi_text = ""

            # MACD
            macd = self.compute_macd(closes)
            if macd is not None:
                hist = macd["histogram"]
                if hist > 0:
                    macd_label = "양전환"
                elif hist < 0:
                    macd_label = "음전환"
                else:
                    macd_label = "중립"
                macd_text = f"MACD: {macd_label} (히스토그램 {hist:+.0f})"
            else:
                macd_text = ""

            indicator_parts = [p for p in [rsi_text, macd_text] if p]
            if indicator_parts:
                lines.append(f"[기술 지표] {' | '.join(indicator_parts)}")

            # Bollinger
            bb = self.compute_bollinger(closes)
            if bb is not None:
                pos_pct = int(bb["position"] * 100)
                if pos_pct >= 80:
                    bb_label = "상단밴드 근처"
                elif pos_pct <= 20:
                    bb_label = "하단밴드 근처"
                else:
                    bb_label = "중간대"
                lines.append(
                    f"[볼린저밴드] {bb_label} (밴드 위치: {pos_pct}%) "
                    f"상단 {bb['upper']:,.0f}원 / 하단 {bb['lower']:,.0f}원"
                )

            # ATR & 동적 손절라인
            atr = self.compute_atr(df)
            if atr is not None and trailing_high is not None:
                dynamic_stop = trailing_high - (atr * atr_multiplier)
                diff_pct = (dynamic_stop - current_price) / current_price * 100
                lines.append(
                    f"[동적 손절라인] {dynamic_stop:,.0f}원 "
                    f"(현재가 대비 {diff_pct:+.1f}%, ATR={atr:,.0f})"
                )

        # 시장 현황
        idx = self.get_market_index_change()
        if idx:
            lines.append(
                f"[시장 현황] KOSPI: {idx['kospi_change_pct']:+.1f}% | "
                f"KOSDAQ: {idx['kosdaq_change_pct']:+.1f}% | "
                f"5일 추세: KOSPI {idx['kospi_5d_trend']}, KOSDAQ {idx['kosdaq_5d_trend']}"
            )

        # 보유 기간
        if holding_days is not None:
            lines.append(f"[보유 기간] {holding_days}일째 보유 중")

        return "\n".join(lines)
