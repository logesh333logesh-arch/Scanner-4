"""
Scanner 4 - MAIN ORCHESTRATOR
--------------------------------
Run this file to execute the full scan:
    1. Load today's access_token (from manual login -> token.txt)
    2. Fetch LIVE spot opening prices (NIFTY, SENSEX, stocks)
    3. Calculate DYNAMIC weekly/monthly expiry dates
    4. Build strike lists for NIFTY, SENSEX (index reducing pattern)
       and high-volume stocks (fixed 5 OTM)
    5. For each strike, fetch daily + weekly option premium OHLC
    6. Run Inverted CPR check (+ Narrow CPR flag) on each
    7. Print/collect all strikes where signal = True

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
    fetch_daily_ohlc,
    fetch_weekly_ohlc,
)
from scanner4_cpr_calculator import check_strike_signal, format_signal_message
from scanner4_telegram import send_signals_to_telegram
from scanner4_live_data import (
    get_index_spot_open,
    get_all_stock_opens,
    get_next_weekly_expiry,
    get_next_monthly_expiry,
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

NARROW_CPR_THRESHOLD_PCT = 0.5   # tune based on backtesting
INVERSION_DEPTH_THRESHOLD_PCT = 20.0  # tuned from live data: clear gap between
                                        # weak inversions (4-14%) and strong ones (40-63%)

STOCK_STRIKE_STEP_DEFAULT = 10   # fallback strike step for stocks not in the override map
STOCK_STRIKE_STEP_OVERRIDE = {
    # TODO: fill in real strike steps per stock as you notice mismatches.
    # Upstox instrument master has the correct step per contract, but for
    # now this is a simple manual override table for known large-cap steps.
    "RELIANCE": 20, "TCS": 20, "HDFCBANK": 10, "INFY": 10,
    "ICICIBANK": 10, "SBIN": 5,
}


def get_previous_trading_day(calendar: list, today: datetime.date) -> datetime.date:
    """Returns the most recent trading day strictly before today (skips weekends/holidays)."""
    earlier_days = [d for d in calendar if d < today]
    if not earlier_days:
        raise ValueError(f"No trading day found before {today} in the calendar")
    return max(earlier_days)


def scan_index(name: str, spot_open: float, expiry: str,
                access_token: str, option_lookup: dict, today: datetime.date) -> list:
    """Runs the full CPR scan for one index (NIFTY or SENSEX)."""
    strikes = build_index_strike_list(name, spot_open, TRADING_DAYS_CALENDAR, today)
    results = []

    prev_day = get_previous_trading_day(TRADING_DAYS_CALENDAR, today)
    week_start = today - datetime.timedelta(days=today.weekday() + 7)  # prev Monday
    week_end = week_start + datetime.timedelta(days=4)                  # prev Friday

    for opt_type, strike_list in strikes.items():
        for strike in strike_list:
            ikey = get_option_instrument_key(option_lookup, name, strike, opt_type, expiry)
            if not ikey:
                print(f"[SKIP] no instrument_key found for {name} {strike} {opt_type} {expiry}")
                continue

            daily_ohlc = fetch_daily_ohlc(access_token, ikey, prev_day)
            weekly_ohlc = fetch_weekly_ohlc(access_token, ikey, week_start, week_end)
            if not daily_ohlc or not weekly_ohlc:
                print(f"[SKIP] missing OHLC data for {name} {strike} {opt_type}")
                continue

            label = f"{name} {int(strike)} {opt_type}"
            result = check_strike_signal(label, daily_ohlc, weekly_ohlc,
                                          NARROW_CPR_THRESHOLD_PCT, INVERSION_DEPTH_THRESHOLD_PCT)
            results.append(result)

    return results


def scan_stocks(access_token: str, option_lookup: dict, today: datetime.date,
                 stock_expiry: str) -> list:
    """Runs the full CPR scan for high-volume F&O stocks (monthly expiry)."""
    top_stocks = get_high_volume_stocks(access_token, top_n=15)
    print(f"[INFO] high volume stocks selected: {top_stocks}")

    key_map = get_instrument_key_map()
    stock_opens = get_all_stock_opens(access_token, top_stocks, key_map)
    print(f"[INFO] live opening prices fetched for {len(stock_opens)}/{len(top_stocks)} stocks")

    results = []
    prev_day = get_previous_trading_day(TRADING_DAYS_CALENDAR, today)
    week_start = today - datetime.timedelta(days=today.weekday() + 7)
    week_end = week_start + datetime.timedelta(days=4)

    for symbol in top_stocks:
        spot_open = stock_opens.get(symbol)
        if not spot_open:
            print(f"[SKIP] no live opening price for {symbol}")
            continue
        strike_step = STOCK_STRIKE_STEP_OVERRIDE.get(symbol, STOCK_STRIKE_STEP_DEFAULT)

        strikes = build_stock_strike_list(spot_open, strike_step)
        for opt_type, strike_list in strikes.items():
            for strike in strike_list:
                ikey = get_option_instrument_key(option_lookup, symbol, strike, opt_type, stock_expiry)
                if not ikey:
                    continue
                daily_ohlc = fetch_daily_ohlc(access_token, ikey, prev_day)
                weekly_ohlc = fetch_weekly_ohlc(access_token, ikey, week_start, week_end)
                if not daily_ohlc or not weekly_ohlc:
                    continue
                label = f"{symbol} {int(strike)} {opt_type}"
                result = check_strike_signal(label, daily_ohlc, weekly_ohlc,
                                              NARROW_CPR_THRESHOLD_PCT, INVERSION_DEPTH_THRESHOLD_PCT)
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
    stock_expiry = get_next_monthly_expiry(today)
    print(f"[INFO] NIFTY spot={nifty_spot} expiry={nifty_expiry}")
    print(f"[INFO] SENSEX spot={sensex_spot} expiry={sensex_expiry}")
    print(f"[INFO] Stock monthly expiry={stock_expiry}")

    print("[STEP 4] Scanning NIFTY...")
    nifty_results = scan_index("NIFTY", nifty_spot, nifty_expiry, access_token, option_lookup, today)

    print("[STEP 5] Scanning SENSEX...")
    sensex_results = scan_index("SENSEX", sensex_spot, sensex_expiry, access_token, option_lookup, today)

    print("[STEP 6] Scanning high-volume stocks...")
    stock_results = scan_stocks(access_token, option_lookup, today, stock_expiry)

    all_results = nifty_results + sensex_results + stock_results
    signals = [r for r in all_results if r["signal"]]

    print(f"\n[DONE] Scanned {len(all_results)} strikes, {len(signals)} REAL signals found "
          f"(depth >= {INVERSION_DEPTH_THRESHOLD_PCT}%).\n")
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
