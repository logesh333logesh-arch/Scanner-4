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


@dataclass
class CPRLevels:
    pivot: float
    bc: float
    tc: float

    @property
    def is_inverted(self) -> bool:
        """BC > Pivot AND TC < Pivot -> inverted CPR condition (our main signal)."""
        return self.bc > self.pivot and self.tc < self.pivot

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
                         narrow_threshold_pct: float = 0.5) -> dict:
    """
    Main function to call per option strike.
    Returns a dict with both CPR levels + whether the inverted condition
    triggered on the DAILY CPR (that's the scan signal), plus weekly
    CPR shown alongside for context (as per your requirement).
    """
    daily_cpr = get_daily_cpr(daily_ohlc)
    weekly_cpr = get_weekly_cpr(weekly_ohlc)

    return {
        "strike": strike_symbol,
        "signal": daily_cpr.is_inverted,          # main trigger condition
        "daily_narrow": daily_cpr.is_narrow(narrow_threshold_pct),
        "weekly_narrow": weekly_cpr.is_narrow(narrow_threshold_pct),
        "daily_cpr": {
            "bc": daily_cpr.bc, "pivot": daily_cpr.pivot, "tc": daily_cpr.tc,
            "inverted": daily_cpr.is_inverted,
        },
        "weekly_cpr": {
            "bc": weekly_cpr.bc, "pivot": weekly_cpr.pivot, "tc": weekly_cpr.tc,
            "inverted": weekly_cpr.is_inverted,
        },
    }


def format_signal_message(result: dict) -> str:
    """Formats a scan result into the Telegram-ready message layout you asked for."""
    d, w = result["daily_cpr"], result["weekly_cpr"]
    d_tag = "Inverted ✓" if d["inverted"] else "Normal"
    w_tag = "Inverted ✓" if w["inverted"] else "Normal"
    lines = [
        f"{result['strike']}",
        f"Daily CPR:  BC={d['bc']} | Pivot={d['pivot']} | TC={d['tc']}  ({d_tag})",
        f"Weekly CPR: BC={w['bc']} | Pivot={w['pivot']} | TC={w['tc']}  ({w_tag})",
    ]
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
