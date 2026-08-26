"""
Scanner 4 - CPR Calculator (Daily + Weekly)
---------------------------------------------
Works on OPTION PREMIUM OHLC (not underlying spot/index OHLC).

CPR formula (standard):
    Pivot = (High + Low + Close) / 3
    BC    = (High + Low) / 2
    TC    = (Pivot - BC) + Pivot   ->  simplifies to  TC = 2*Pivot - BC

Normal CPR order:  BC < Pivot < TC
Inverted CPR (what we're scanning for):  BC > Pivot  AND  TC < Pivot
    -> means BC > TC as well (levels have flipped)

Narrow CPR (separate/optional filter):
    width = TC - BC
    narrow if width <= (some % of Pivot, e.g. 0.5%) - threshold configurable
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OHLC:
    high: float
    low: float
    close: float
    open: float = None    # optional - needed for % change / color indicator
    volume: float = None  # optional - needed for volume display


@dataclass
class CPRLevels:
    pivot: float
    bc: float
    tc: float

    @property
    def is_inverted(self) -> bool:
        """BC > Pivot AND TC < Pivot -> inverted CPR condition (our main signal)."""
        return self.bc > self.pivot and self.tc < self.pivot

    def is_strong_inverted(self, min_depth_pct: float = 3.0) -> bool:
        """
        Stricter version of is_inverted: only True if the inversion is
        'deep enough' to be meaningful, not just a marginal BC>Pivot flip.

        min_depth_pct: (BC - TC) as a % of Pivot, must be >= this to count.
        Plain is_inverted() triggers on ~40-50% of random candles (whenever
        close < midpoint of high-low) - too noisy to trade on. This filter
        keeps only strikes with a real, visible CPR inversion.

        Tune min_depth_pct based on backtesting - start around 3-5% and
        adjust based on how many signals per day you actually want.
        """
        if not self.is_inverted or self.pivot == 0:
            return False
        depth_pct = ((self.bc - self.tc) / self.pivot) * 100
        return depth_pct >= min_depth_pct

    @property
    def width(self) -> float:
        return abs(self.tc - self.bc)

    def is_narrow(self, threshold_pct: float = 0.5) -> bool:
        """
        threshold_pct: width as % of pivot below which CPR is considered 'narrow'.
        Default 0.5% - tune this based on backtesting (same idea as Scanner 3).
        """
        if self.pivot == 0:
            return False
        return (self.width / self.pivot) * 100 <= threshold_pct


def calculate_cpr(ohlc: OHLC) -> CPRLevels:
    """Core CPR formula - works for both daily and weekly OHLC input."""
    pivot = (ohlc.high + ohlc.low + ohlc.close) / 3
    bc = (ohlc.high + ohlc.low) / 2
    tc = (2 * pivot) - bc
    return CPRLevels(pivot=round(pivot, 2), bc=round(bc, 2), tc=round(tc, 2))


def get_daily_cpr(prev_day_ohlc: OHLC) -> CPRLevels:
    """
    Daily CPR = calculated from PREVIOUS trading day's option premium OHLC.
    Used to judge TODAY's price action.
    """
    return calculate_cpr(prev_day_ohlc)


def get_weekly_cpr(prev_week_ohlc: OHLC) -> CPRLevels:
    """
    Weekly CPR = calculated from PREVIOUS week's option premium OHLC
    (Mon-Fri high/low/close of the option, aggregated).
    Used to judge THIS week's price action / context.
    """
    return calculate_cpr(prev_week_ohlc)


def aggregate_weekly_ohlc(daily_candles: list) -> OHLC:
    """
    daily_candles: list of OHLC objects for each trading day of the PREVIOUS week
    (Mon-Fri). Aggregates into one weekly OHLC:
        weekly high  = max of daily highs
        weekly low   = min of daily lows
        weekly close = last day's close (Friday's close)
    """
    if not daily_candles:
        raise ValueError("daily_candles list is empty - can't build weekly OHLC")
    weekly_high = max(c.high for c in daily_candles)
    weekly_low = min(c.low for c in daily_candles)
    weekly_close = daily_candles[-1].close  # last trading day of that week
    return OHLC(high=weekly_high, low=weekly_low, close=weekly_close)


def check_strike_signal(strike_symbol: str,
                         daily_ohlc: OHLC,
                         weekly_ohlc: OHLC,
                         narrow_threshold_pct: float = 0.5,
                         inversion_depth_pct: float = 3.0) -> dict:
    """
    Main function to call per option strike.
    Returns a dict with both CPR levels + whether the STRONG inverted
    condition triggered on the DAILY CPR (that's the scan signal - uses
    is_strong_inverted, not the raw is_inverted, to avoid noisy ~50% hit
    rate), plus weekly CPR shown alongside for context.
    """
    daily_cpr = get_daily_cpr(daily_ohlc)
    weekly_cpr = get_weekly_cpr(weekly_ohlc)

    return {
        "strike": strike_symbol,
        "signal": daily_cpr.is_strong_inverted(inversion_depth_pct),  # main trigger condition
        "daily_narrow": daily_cpr.is_narrow(narrow_threshold_pct),
        "weekly_narrow": weekly_cpr.is_narrow(narrow_threshold_pct),
        "daily_cpr": {
            "bc": daily_cpr.bc, "pivot": daily_cpr.pivot, "tc": daily_cpr.tc,
            "inverted": daily_cpr.is_inverted,
            "strong_inverted": daily_cpr.is_strong_inverted(inversion_depth_pct),
        },
        "weekly_cpr": {
            "bc": weekly_cpr.bc, "pivot": weekly_cpr.pivot, "tc": weekly_cpr.tc,
            "inverted": weekly_cpr.is_inverted,
            "strong_inverted": weekly_cpr.is_strong_inverted(inversion_depth_pct),
        },
        "daily_price": {"open": daily_ohlc.open, "close": daily_ohlc.close, "volume": daily_ohlc.volume},
    }


def classify_volume(current_vol: float, avg_vol: float) -> str:
    """
    Classifies current day's volume relative to its own recent average
    (20-day, computed by the caller) and returns the emoji + label.
        < 0.75x avg  -> Low
        0.75x-1.5x   -> Mid range
        1.5x-2.0x    -> High
        > 2.0x       -> Very High
    """
    if not avg_vol:
        return ""
    ratio = current_vol / avg_vol
    if ratio > 2.0:
        return "🚨 Very High"
    if ratio > 1.5:
        return "🔋 High"
    if ratio >= 0.75:
        return "🎚️ Mid range"
    return "🪫 Low"


def format_signal_message(result: dict) -> str:
    """Formats a scan result into the Telegram-ready message layout,
    using emoji in place of text labels (Daily/Weekly CPR, strength,
    depth, volume tier) per the requested style."""
    d, w = result["daily_cpr"], result["weekly_cpr"]

    def tag_with_depth(cpr_dict, bc, tc, pivot):
        if not cpr_dict["inverted"]:
            return "⏸️ Normal"
        depth_pct = ((bc - tc) / pivot) * 100 if pivot else 0
        marker = "💪" if cpr_dict["strong_inverted"] else "🚾"
        return f"{marker} ♨️{depth_pct:.1f}%"

    d_tag = tag_with_depth(d, d["bc"], d["tc"], d["pivot"])
    w_tag = tag_with_depth(w, w["bc"], w["tc"], w["pivot"])

    lines = [f"{result['strike']}"]

    # Price move indicator (color-coded up/down)
    price = result.get("daily_price")
    if price and price.get("open") and price.get("close"):
        open_p, close_p = price["open"], price["close"]
        pct_change = ((close_p - open_p) / open_p) * 100 if open_p else 0
        arrow = "🟢▲" if pct_change >= 0 else "🔴▼"
        lines.append(f"{arrow} {pct_change:+.1f}% (O={open_p} → C={close_p})")

    # Volume tier (needs avg_volume attached by caller - see scanner4_main.py)
    vol_stats = result.get("volume_stats")
    if vol_stats and vol_stats.get("current") and vol_stats.get("avg"):
        vol_label = classify_volume(vol_stats["current"], vol_stats["avg"])
        if vol_label:
            lines.append(vol_label)

    lines.append(f"🚥 {d['bc']} | {d['pivot']} | {d['tc']}  {d_tag}")
    lines.append(f"🧮 {w['bc']} | {w['pivot']} | {w['tc']}  {w_tag}")
    return "\n".join(lines)


# ============================================================
# QUICK TEST
# ============================================================
if __name__ == "__main__":
    # Example 1: Normal CPR (no signal)
    normal_day = OHLC(high=48.0, low=40.0, close=44.0)
    cpr1 = calculate_cpr(normal_day)
    print("Normal case:", cpr1, "| inverted:", cpr1.is_inverted)

    # Example 2: Inverted CPR (should trigger signal)
    # need high/low/close combo where BC > Pivot and TC < Pivot
    inverted_day = OHLC(high=50.0, low=48.0, close=30.0)
    cpr2 = calculate_cpr(inverted_day)
    print("Inverted case:", cpr2, "| inverted:", cpr2.is_inverted)

    # Full strike test
    weekly_candles = [
        OHLC(high=42, low=35, close=38),
        OHLC(high=45, low=36, close=40),
        OHLC(high=47, low=38, close=41),
        OHLC(high=46, low=37, close=39),
        OHLC(high=44, low=36, close=37),  # Friday close
    ]
    weekly = aggregate_weekly_ohlc(weekly_candles)
    result = check_strike_signal("NIFTY 24900 CE", inverted_day, weekly)
    print("\n--- Full Result ---")
    print(result)
    print("\n--- Telegram Message Format ---")
    print(format_signal_message(result))
