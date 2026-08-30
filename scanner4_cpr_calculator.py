"""
Scanner 4 - CPR Calculator (SIMPLIFIED CONDITION SET)
---------------------------------------------------------
Works on OPTION PREMIUM OHLC (not underlying spot/index OHLC).

CPR formula (standard):
    Pivot = (High + Low + Close) / 3
    BC    = (High + Low) / 2
    TC    = 2*Pivot - BC

SIGNAL CONDITION (only two things matter - nothing else):
    1. Narrow CPR (daily only) - TC-BC gap is tight relative to Pivot,
       indicating consolidation / a coiled range.
    2. Full-body candle closing near the top (daily) - a strong bullish
       candle where the body dominates the day's range and there's
       almost no upper wick (i.e. price closed near its high).

Both must be true on the SAME strike's daily option-premium candle for
an alert. No Inverted CPR, no weekly CPR, no depth% gating - those were
removed per your request.
"""

from dataclasses import dataclass


@dataclass
class OHLC:
    high: float
    low: float
    close: float
    open: float = None    # needed for candle-body condition + % change display
    volume: float = None  # needed for volume tier display


@dataclass
class CPRLevels:
    pivot: float
    bc: float
    tc: float

    @property
    def width(self) -> float:
        return abs(self.tc - self.bc)

    def is_narrow(self, threshold_pct: float = 0.5) -> bool:
        """
        threshold_pct: width as % of pivot below which CPR is considered
        'narrow'. Default 0.5% - tune this based on backtesting; raise it
        if too few/no signals are firing, lower it if too many are.
        """
        if self.pivot == 0:
            return False
        return (self.width / self.pivot) * 100 <= threshold_pct


def calculate_cpr(ohlc: OHLC) -> CPRLevels:
    """Core CPR formula."""
    pivot = (ohlc.high + ohlc.low + ohlc.close) / 3
    bc = (ohlc.high + ohlc.low) / 2
    tc = (2 * pivot) - bc
    return CPRLevels(pivot=round(pivot, 2), bc=round(bc, 2), tc=round(tc, 2))


def get_daily_cpr(prev_day_ohlc: OHLC) -> CPRLevels:
    """Daily CPR = calculated from PREVIOUS trading day's option premium OHLC."""
    return calculate_cpr(prev_day_ohlc)


def is_full_body_near_top(ohlc: OHLC, min_body_ratio: float = 0.70,
                           max_upper_wick_ratio: float = 0.10) -> bool:
    """
    Checks if the day's candle is a strong bullish candle that closed
    near its high (a 'full body, near-top close' candle - similar to a
    bullish Marubozu):
        - close > open (bullish)
        - body (close-open) is at least `min_body_ratio` of the day's
          full range (high-low) - i.e. the candle isn't mostly wick
        - upper wick (high - close) is at most `max_upper_wick_ratio`
          of the day's range - i.e. price closed very near the high

    Requires ohlc.open to be set (fetched from the historical candle's
    open field). Returns False if open is missing or range is zero.

    Tune min_body_ratio (0.70 default) and max_upper_wick_ratio (0.10
    default) based on backtesting - raise min_body_ratio for stricter
    "full body" requirement, lower max_upper_wick_ratio for stricter
    "near the top" requirement.
    """
    if ohlc.open is None:
        return False
    day_range = ohlc.high - ohlc.low
    if day_range <= 0:
        return False
    if ohlc.close <= ohlc.open:  # must be bullish
        return False
    body = ohlc.close - ohlc.open
    upper_wick = ohlc.high - ohlc.close
    body_ratio = body / day_range
    upper_wick_ratio = upper_wick / day_range
    return body_ratio >= min_body_ratio and upper_wick_ratio <= max_upper_wick_ratio


def check_strike_signal(strike_symbol: str,
                         daily_ohlc: OHLC,
                         narrow_threshold_pct: float = 0.5,
                         min_body_ratio: float = 0.70,
                         max_upper_wick_ratio: float = 0.10) -> dict:
    """
    Main function to call per option strike.
    Signal fires ONLY when BOTH are true on the daily option-premium
    candle:
        1. Narrow CPR (daily)
        2. Full-body candle closing near the top
    No other condition (Inverted CPR, weekly CPR, depth%) is used.
    """
    daily_cpr = get_daily_cpr(daily_ohlc)
    narrow_ok = daily_cpr.is_narrow(narrow_threshold_pct)
    candle_ok = is_full_body_near_top(daily_ohlc, min_body_ratio, max_upper_wick_ratio)

    return {
        "strike": strike_symbol,
        "signal": narrow_ok and candle_ok,
        "narrow_cpr": narrow_ok,
        "full_body_candle": candle_ok,
        "daily_cpr": {"bc": daily_cpr.bc, "pivot": daily_cpr.pivot, "tc": daily_cpr.tc},
        "daily_price": {"open": daily_ohlc.open, "close": daily_ohlc.close,
                         "high": daily_ohlc.high, "low": daily_ohlc.low,
                         "volume": daily_ohlc.volume},
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
    """
    Formats a scan result into the Telegram-ready message. Only shows
    what matters for this condition set: the strike, price move, volume
    tier, the Narrow CPR levels, and the candle confirmation - nothing
    about Inverted CPR, weekly CPR, or depth%.
    """
    d = result["daily_cpr"]
    lines = [f"{result['strike']}"]

    price = result.get("daily_price")
    if price and price.get("open") and price.get("close"):
        open_p, close_p = price["open"], price["close"]
        pct_change = ((close_p - open_p) / open_p) * 100 if open_p else 0
        arrow = "🟢▲" if pct_change >= 0 else "🔴▼"
        lines.append(f"{arrow} {pct_change:+.1f}% (O={open_p} → C={close_p})")

    vol_stats = result.get("volume_stats")
    if vol_stats and vol_stats.get("current") and vol_stats.get("avg"):
        vol_label = classify_volume(vol_stats["current"], vol_stats["avg"])
        if vol_label:
            lines.append(vol_label)

    narrow_tag = "✅" if result["narrow_cpr"] else "❌"
    candle_tag = "✅" if result["full_body_candle"] else "❌"
    lines.append(f"🚥 {d['bc']} | {d['pivot']} | {d['tc']}  🎯 Narrow CPR {narrow_tag}")
    lines.append(f"🕯️ Full-body top close {candle_tag}")

    return "\n".join(lines)


# ============================================================
# QUICK TEST
# ============================================================
if __name__ == "__main__":
    # Narrow CPR + strong full-body bullish candle closing near top -> SHOULD signal
    good_day = OHLC(high=50.0, low=45.0, close=49.5, open=45.5, volume=125000)
    result = check_strike_signal("NIFTY 24900 CE", good_day)
    print(format_signal_message(result))
    print("Signal:", result["signal"])
    print()

    # Wide CPR (not narrow) -> should NOT signal even with a good candle
    wide_day = OHLC(high=80.0, low=40.0, close=78.0, open=42.0, volume=125000)
    result2 = check_strike_signal("NIFTY 24950 CE", wide_day)
    print(format_signal_message(result2))
    print("Signal:", result2["signal"])
