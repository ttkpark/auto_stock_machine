"""매수 후보 스크리너.

사용자 정의 룰에 따라 KRX 전체를 스크리닝하여 AI에게 제시할 후보 리스트를 생성합니다.

룰 (모두 AND):
 1. 최근 10캔들 평균 거래대금 ≥ 3억
 2. 시총 기준 급등 캔들 존재
    - 시총 1조 이하: 최근 30캔들 중 (고가-종가)/종가×100 ∈ [8, 30]
    - 시총 1~5조: 최근 30캔들 중 (고가-종가)/종가×100 ∈ [12, 30]
    - 5조 초과: 스크리너 대상 외
 3. 최근 1~2달(40봉) 거래량 평균 ≥ 1년(240봉) 거래량 평균 × 300%
 4. 최근 60봉 내 박스권 상단 돌파 이력: 어느 시점에
    당일 종가 > 직전 20봉 고가 AND 당일 거래량 ≥ 5일 평균 × 300%

추가 플래그 (필터 아닌 정보성):
 - buy_point: 오늘 종가가 20일선 대비 -4 ~ -3% (매수 타점)
 - breakout_today: 오늘이 박스권 돌파일
 - kosdaq_debt_risk: KOSDAQ이고 4년 부채 조건 해당 (※ 현재 데이터 소스 미연동 → 항상 None)
"""
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

MARCAP_TIER_1 = 1_000_000_000_000   # 1조
MARCAP_TIER_2 = 5_000_000_000_000   # 5조
MIN_AMOUNT_10D = 300_000_000        # 최근 10캔들 평균 거래대금 3억


def _gain_range_for_marcap(marcap: float) -> tuple[float, float] | None:
    if marcap <= MARCAP_TIER_1:
        return (8.0, 30.0)
    if marcap <= MARCAP_TIER_2:
        return (12.0, 30.0)
    return None


def _int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def screen_buy_candidates(
    max_candidates: int = 30,
    pre_universe_cap: int | None = None,
) -> list[dict]:
    """KRX 전체에서 룰을 만족하는 종목을 스크리닝합니다.

    pre_universe_cap: 일봉 조회 전 사전 필터 후 상위 N개까지만 본스캔 (속도 제한).
                      None이면 env SCREENER_UNIVERSE_CAP (기본 150) 사용.
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        logger.warning("[Screener] FinanceDataReader 미설치 — 스크리너 비활성")
        return []

    try:
        krx = fdr.StockListing("KRX")
    except Exception as e:
        logger.warning(f"[Screener] KRX 목록 조회 실패: {e}")
        return []

    if "Marcap" not in krx.columns or "Code" not in krx.columns:
        logger.warning("[Screener] KRX 목록에 Marcap/Code 컬럼 없음 — 스크리너 중단")
        return []

    amount_col = next(
        (c for c in krx.columns if c.lower() in ("amount", "tradingvalue", "tradingamount")),
        None,
    )
    market_col = next((c for c in krx.columns if c.lower() == "market"), None)

    pre = krx.dropna(subset=["Marcap"]).copy()
    pre = pre[(pre["Marcap"] > 0) & (pre["Marcap"] <= MARCAP_TIER_2)]
    if amount_col is not None:
        pre = pre[pre[amount_col].fillna(0) >= MIN_AMOUNT_10D]

    sort_col = amount_col if amount_col is not None else "Marcap"
    cap = pre_universe_cap if pre_universe_cap is not None else _int_env("SCREENER_UNIVERSE_CAP", 150)
    pre = pre.sort_values(sort_col, ascending=False).head(cap)

    logger.info(f"[Screener] 사전 필터 통과 {len(pre)}개 → 본스캔 시작")

    end = datetime.now()
    start = end - timedelta(days=400)
    results: list[dict] = []

    for _, row in pre.iterrows():
        code = str(row["Code"]).strip()
        name = str(row.get("Name", "")).strip()
        try:
            marcap = float(row["Marcap"])
        except (TypeError, ValueError):
            continue
        market = str(row.get(market_col, "")).strip() if market_col else ""

        try:
            df = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        except Exception:
            continue
        if df is None or len(df) < 60:
            continue

        closes = df["Close"]
        highs = df["High"]
        volumes = df["Volume"]
        amounts = df["Amount"] if "Amount" in df.columns else (closes * volumes)

        # Rule 1: 최근 10캔들 평균 거래대금 ≥ 3억
        amount_10d = float(amounts.iloc[-10:].mean())
        if amount_10d < MIN_AMOUNT_10D:
            continue

        # Rule 2: 시총 구간별 급등 캔들 존재
        gain_range = _gain_range_for_marcap(marcap)
        if gain_range is None:
            continue
        gain_pct = (highs.iloc[-30:] - closes.iloc[-30:]) / closes.iloc[-30:] * 100
        if not ((gain_pct >= gain_range[0]) & (gain_pct <= gain_range[1])).any():
            continue

        # Rule 3: 최근 40봉 평균 거래량 ≥ 1년 평균 × 300%
        vol_recent = float(volumes.iloc[-40:].mean())
        vol_year = float(volumes.iloc[-240:].mean()) if len(volumes) >= 240 else float(volumes.mean())
        if vol_year <= 0 or vol_recent < vol_year * 3:
            continue

        # Rule 4: 최근 60봉 내 박스권 상단 돌파 이력
        breakout_any = False
        breakout_today = False
        scan_start = max(20, len(df) - 60)
        for i in range(scan_start, len(df)):
            if i < 20:
                continue
            prev_20_high = float(highs.iloc[i - 20 : i].max())
            day_close = float(closes.iloc[i])
            if day_close <= prev_20_high:
                continue
            vol5_end = i
            vol5_start = max(0, i - 5)
            vol5_avg = float(volumes.iloc[vol5_start:vol5_end].mean()) if vol5_end > vol5_start else 0.0
            day_vol = float(volumes.iloc[i])
            if vol5_avg > 0 and day_vol >= vol5_avg * 3:
                breakout_any = True
                if i == len(df) - 1:
                    breakout_today = True
        if not breakout_any:
            continue

        # 정보성 지표
        today_close = float(closes.iloc[-1])
        today_vol = float(volumes.iloc[-1])
        prev_20_high_today = float(highs.iloc[-21:-1].max())
        vol5_avg_today = float(volumes.iloc[-6:-1].mean())
        ma20 = float(closes.iloc[-20:].mean())
        ma20_diff_pct = ((today_close - ma20) / ma20 * 100) if ma20 > 0 else 0.0
        buy_point = -4.0 <= ma20_diff_pct <= -3.0

        results.append(
            {
                "name": name,
                "code": code,
                "market": market,
                "marcap": int(marcap),
                "close": int(today_close),
                "amount_10d": int(amount_10d),
                "vol_ratio_year": round(vol_recent / vol_year, 2) if vol_year > 0 else 0.0,
                "vol_ratio_5d_today": round(today_vol / vol5_avg_today, 2) if vol5_avg_today > 0 else 0.0,
                "prev_20_high": int(prev_20_high_today),
                "ma20": int(ma20),
                "ma20_diff_pct": round(ma20_diff_pct, 1),
                "gain_range": f"{gain_range[0]:.0f}~{gain_range[1]:.0f}%",
                "breakout_today": breakout_today,
                "buy_point": buy_point,
                "kosdaq_debt_risk": None,  # TODO: OpenDART 연동 후 채움
            }
        )
        if len(results) >= max_candidates:
            break

    logger.info(f"[Screener] 최종 후보 {len(results)}개")
    return results


def format_candidates_for_prompt(candidates: list[dict]) -> str:
    if not candidates:
        return "[스크리너 결과] 오늘은 룰을 통과한 후보가 없습니다."

    lines = [f"[스크리너 통과 {len(candidates)}개 후보]"]
    for i, c in enumerate(candidates, 1):
        marcap_trillion = c["marcap"] / 1e12
        amount_eok = c["amount_10d"] / 1e8
        tags = []
        if c.get("breakout_today"):
            tags.append("오늘 박스권돌파")
        if c.get("buy_point"):
            tags.append("20일선 매수타점")
        if c.get("kosdaq_debt_risk") is True:
            tags.append("KOSDAQ 부채주의")
        tag_str = f" [{' · '.join(tags)}]" if tags else ""

        lines.append(
            f"{i:>2}. {c['name']} ({c['code']}·{c['market']}) "
            f"시총 {marcap_trillion:.2f}조 / 현재가 {c['close']:,}원 / "
            f"10일 평균거래대금 {amount_eok:.1f}억 / "
            f"최근 1~2달 거래량 연평균 대비 ×{c['vol_ratio_year']} / "
            f"20일선 {c['ma20']:,}원(대비 {c['ma20_diff_pct']:+.1f}%) / "
            f"직전 20일 고가 {c['prev_20_high']:,}원(오늘 5일 거래량배수 ×{c['vol_ratio_5d_today']}) / "
            f"최근 30봉 급등캔들 {c['gain_range']} 구간 존재{tag_str}"
        )
    return "\n".join(lines)
