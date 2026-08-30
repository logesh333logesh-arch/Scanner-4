"""
Scanner 4 - MAIN ORCHESTRATOR (SIMPLIFIED CONDITION SET)
--------------------------------------------------------------
Run this file to execute the full scan:
    1. Load today's access_token (from manual login -> token.txt)
    2. Fetch LIVE spot opening prices (NIFTY, SENSEX, stocks)
    3. Calculate DYNAMIC weekly/monthly expiry dates
    4. Build strike lists for NIFTY, SENSEX (index reducing pattern)
       and high-volume stocks (fixed 5 OTM)
    5. For each strike, fetch daily option premium OHLC (weekly OHLC no
       longer needed - condition set uses daily CPR + daily candle only)
    6. Run signal check: Narrow CPR (daily) AND full-body candle closing
       near the top - both must be true, nothing else matters
    7. Print/collect all strikes where signal = True, send to Telegram

Usage:
    python scanner4_main.py
    (run AFTER 9:15 AM IST so today's opening prices are available)

Prerequisites (must run once before this, same day):
    1. python scanner4_manual_login.py     -> creates token.txt
    2. download_instrument_master()        -> refreshes complete.json (auto-called below)
"""

import datetime
from scanner4_manual_login import load_token
from scanner4_strike_volume import (
    build_index_strike_list,
    build_stock_strike_list,
    get_high_volume_stocks,
    get_instrument_key_map,
)
from scanner4_option_data import (
    download_instrument_master,
    build_option_lookup,
    get_option_instrument_key,
    get_dynamic_strike_step,
    get_next_expiry_for_symbol,
    fetch_daily_ohlc,
    fetch_volume_stats,
)
from scanner4_cpr_calculator import check_strike_signal, format_signal_message
from scanner4_telegram import send_signals_to_telegram
from scanner4_live_data import (
    get_index_spot_open,
    get_all_stock_opens,
    get_next_weekly_expiry,
)

# ============================================================
# CONFIG
# ============================================================

TRADING_DAYS_CALENDAR = [
    # TODO: replace with a real NSE trading-day calendar (holidays excluded).
    # This placeholder generates weekdays for the current + next month, which
    # is good enough for the strike-count pattern but doesn't skip holidays.
    datetime.date(2026, 8, d) for d in range(1, 32)
    if datetime.date(2026, 8, d).weekday() < 5
] + [
    datetime.date(2026, 9, d) for d in range(1, 31)
    if datetime.date(2026, 9, d).weekday() < 5
]

# ---- Signal condition thresholds (tune based on backtesting) ----
NARROW_CPR_THRESHOLD_PCT = 0.5     # daily CPR width as % of pivot, must be <= this
FULL_BODY_MIN_RATIO = 0.70          # candle body must be >= 70% of the day's range
MAX_UPPER_WICK_RATIO = 0.10         # upper wick must be <= 10% of the day's range
# NOTE: stock strike step is derived automatically per-symbol from the
# live instrument master (see get_dynamic_strike_step) - no manual table needed.


def get_previous_trading_day(calendar: list, today: datetime.date) -> datetime.date:
    """Returns the most recent trading day strictly before today (skips weekends/holidays)."""
    earlier_days = [d for d in calendar if d < today]
    if not earlier_days:
        raise ValueError(f"No trading day found before {today} in the calendar")
    return max(earlier_days)


def scan_index(name: str, spot_open: float, expiry: str,
                access_token: str, option_lookup: dict, today: datetime.date) -> list:
    """Runs the simplified CPR+candle scan for one index (NIFTY or SENSEX)."""
    strikes = build_index_strike_list(name, spot_open, TRADING_DAYS_CALENDAR, today)
    results = []
    prev_day = get_previous_trading_day(TRADING_DAYS_CALENDAR, today)

    for opt_type, strike_list in strikes.items():
        for strike in strike_list:
            ikey = get_option_instrument_key(option_lookup, name, strike, opt_type, expiry)
            if not ikey:
                print(f"[SKIP] no instrument_key found for {name} {strike} {opt_type} {expiry}")
                continue

            daily_ohlc = fetch_daily_ohlc(access_token, ikey, prev_day)
            if not daily_ohlc:
                print(f"[SKIP] missing OHLC data for {name} {strike} {opt_type}")
                continue

            label = f"{name} {int(strike)} {opt_type}"
            result = check_strike_signal(label, daily_ohlc, NARROW_CPR_THRESHOLD_PCT,
                                          FULL_BODY_MIN_RATIO, MAX_UPPER_WICK_RATIO)
            result["volume_stats"] = fetch_volume_stats(access_token, ikey, prev_day)
            results.append(result)

    return results


def scan_stocks(access_token: str, option_lookup: dict, today: datetime.date) -> list:
    """Runs the simplified CPR+candle scan for high-volume F&O stocks (monthly expiry)."""
    top_stocks = get_high_volume_stocks(access_token, top_n=15)
    print(f"[INFO] high volume stocks selected: {top_stocks}")

    key_map = get_instrument_key_map()
    stock_opens = get_all_stock_opens(access_token, top_stocks, key_map)
    print(f"[INFO] live opening prices fetched for {len(stock_opens)}/{len(top_stocks)} stocks")

    results = []
    prev_day = get_previous_trading_day(TRADING_DAYS_CALENDAR, today)

    for symbol in top_stocks:
        spot_open = stock_opens.get(symbol)
        if not spot_open:
            print(f"[SKIP] no live opening price for {symbol}")
            continue

        symbol_expiry = get_next_expiry_for_symbol(option_lookup, symbol, today)
        if not symbol_expiry:
            print(f"[SKIP] no option contracts found for {symbol} at all")
            continue

        strike_step = get_dynamic_strike_step(option_lookup, symbol, symbol_expiry)
        if not strike_step:
            print(f"[SKIP] could not determine strike step for {symbol} "
                  f"(no contracts found for expiry {symbol_expiry})")
            continue

        strikes = build_stock_strike_list(spot_open, strike_step)
        for opt_type, strike_list in strikes.items():
            for strike in strike_list:
                ikey = get_option_instrument_key(option_lookup, symbol, strike, opt_type, symbol_expiry)
                if not ikey:
                    print(f"[SKIP] no instrument_key for {symbol} {strike} {opt_type}")
                    continue
                daily_ohlc = fetch_daily_ohlc(access_token, ikey, prev_day)
                if not daily_ohlc:
                    continue
                label = f"{symbol} {int(strike)} {opt_type}"
                result = check_strike_signal(label, daily_ohlc, NARROW_CPR_THRESHOLD_PCT,
                                              FULL_BODY_MIN_RATIO, MAX_UPPER_WICK_RATIO)
                result["volume_stats"] = fetch_volume_stats(access_token, ikey, prev_day)
                results.append(result)

    return results


def main():
    today = datetime.date.today()
    access_token = load_token()

    print("[STEP 1] Refreshing instrument master...")
    download_instrument_master()

    print("[STEP 2] Building option lookup table...")
    option_lookup = build_option_lookup()

    print("[STEP 3] Fetching live spot prices + expiry dates...")
    nifty_spot = get_index_spot_open(access_token, "NIFTY")
    sensex_spot = get_index_spot_open(access_token, "SENSEX")
    nifty_expiry = get_next_weekly_expiry("NIFTY", today)
    sensex_expiry = get_next_weekly_expiry("SENSEX", today)
    print(f"[INFO] NIFTY spot={nifty_spot} expiry={nifty_expiry}")
    print(f"[INFO] SENSEX spot={sensex_spot} expiry={sensex_expiry}")

    print("[STEP 4] Scanning NIFTY...")
    nifty_results = scan_index("NIFTY", nifty_spot, nifty_expiry, access_token, option_lookup, today)

    print("[STEP 5] Scanning SENSEX...")
    sensex_results = scan_index("SENSEX", sensex_spot, sensex_expiry, access_token, option_lookup, today)

    print("[STEP 6] Scanning high-volume stocks...")
    stock_results = scan_stocks(access_token, option_lookup, today)

    all_results = nifty_results + sensex_results + stock_results
    signals = [r for r in all_results if r["signal"]]

    print(f"\n[DONE] Scanned {len(all_results)} strikes, {len(signals)} signals found "
          f"(Narrow CPR + full-body top-close candle).\n")
    print("=" * 50)
    print("SIGNAL STRIKES ONLY:")
    print("=" * 50)
    for r in signals:
        print(format_signal_message(r))
        print("-" * 40)

    print("\n[STEP 7] Sending results to Telegram...")
    send_signals_to_telegram(signals)

    return signals


if __name__ == "__main__":
    main()
