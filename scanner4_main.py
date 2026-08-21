"""
Scanner 4 - MAIN ORCHESTRATOR
--------------------------------
Run this file to execute the full scan:
    1. Load today's access_token (from manual login -> token.txt)
    2. Build strike lists for NIFTY, SENSEX (index reducing pattern)
       and high-volume stocks (fixed 5 OTM)
    3. For each strike, fetch daily + weekly option premium OHLC
    4. Run Inverted CPR check (+ Narrow CPR flag) on each
    5. Print/collect all strikes where signal = True

Usage:
    python scanner4_main.py

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
)
from scanner4_option_data import (
    download_instrument_master,
    build_option_lookup,
    get_option_instrument_key,
    fetch_daily_ohlc,
    fetch_weekly_ohlc,
)
from scanner4_cpr_calculator import check_strike_signal, format_signal_message

# ============================================================
# CONFIG - fill these in before running
# ============================================================

TRADING_DAYS_CALENDAR = [
    # TODO: replace with a real NSE trading-day calendar (holidays excluded)
    # for now, generating weekdays for the current month as a placeholder
    datetime.date(2026, 8, d) for d in range(1, 32)
    if datetime.date(2026, 8, d).weekday() < 5
]

NIFTY_SPOT_OPEN = 24837.0     # TODO: fetch live today's opening spot price
SENSEX_SPOT_OPEN = 81250.0    # TODO: fetch live today's opening spot price
NIFTY_EXPIRY = "25-08-2026"   # TODO: today's relevant weekly expiry, DD-MM-YYYY
SENSEX_EXPIRY = "27-08-2026"  # TODO: today's relevant weekly expiry, DD-MM-YYYY
STOCK_EXPIRY = "24-09-2026"   # TODO: current monthly expiry, DD-MM-YYYY

NARROW_CPR_THRESHOLD_PCT = 0.5  # tune based on backtesting
INVERSION_DEPTH_THRESHOLD_PCT = 20.0  # tuned from live data: clear gap between
                                        # weak inversions (4-14%) and strong ones (40-63%)
INVERSION_DEPTH_PCT = 20.0      # based on observed data: noise clusters under ~15%,
                                 # real signals cluster 40%+. 20% is a safe cutoff.
                                 # Adjust after a few more days of live output.


def scan_index(name: str, spot_open: float, expiry: str,
                access_token: str, option_lookup: dict, today: datetime.date) -> list:
    """Runs the full CPR scan for one index (NIFTY or SENSEX)."""
    strikes = build_index_strike_list(name, spot_open, TRADING_DAYS_CALENDAR, today)
    results = []

    prev_day = today - datetime.timedelta(days=1)
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
            result = check_strike_signal(label, daily_ohlc, weekly_ohlc, NARROW_CPR_THRESHOLD_PCT, INVERSION_DEPTH_THRESHOLD_PCT)
            results.append(result)

    return results


def scan_stocks(access_token: str, option_lookup: dict, today: datetime.date) -> list:
    """Runs the full CPR scan for high-volume F&O stocks (monthly expiry)."""
    top_stocks = get_high_volume_stocks(access_token, top_n=15)
    print(f"[INFO] high volume stocks selected: {top_stocks}")

    results = []
    prev_day = today - datetime.timedelta(days=1)
    week_start = today - datetime.timedelta(days=today.weekday() + 7)
    week_end = week_start + datetime.timedelta(days=4)

    for symbol in top_stocks:
        # TODO: replace with live spot open fetch per stock, and correct strike step per stock
        spot_open = 1500.0   # placeholder - must fetch live
        strike_step = 20      # placeholder - varies per stock, needs a lookup

        strikes = build_stock_strike_list(spot_open, strike_step)
        for opt_type, strike_list in strikes.items():
            for strike in strike_list:
                ikey = get_option_instrument_key(option_lookup, symbol, strike, opt_type, STOCK_EXPIRY)
                if not ikey:
                    continue
                daily_ohlc = fetch_daily_ohlc(access_token, ikey, prev_day)
                weekly_ohlc = fetch_weekly_ohlc(access_token, ikey, week_start, week_end)
                if not daily_ohlc or not weekly_ohlc:
                    continue
                label = f"{symbol} {int(strike)} {opt_type}"
                result = check_strike_signal(label, daily_ohlc, weekly_ohlc, NARROW_CPR_THRESHOLD_PCT, INVERSION_DEPTH_THRESHOLD_PCT)
                results.append(result)

    return results


def main():
    today = datetime.date.today()
    access_token = load_token()

    print("[STEP 1] Refreshing instrument master...")
    download_instrument_master()

    print("[STEP 2] Building option lookup table...")
    option_lookup = build_option_lookup()

    print("[STEP 3] Scanning NIFTY...")
    nifty_results = scan_index("NIFTY", NIFTY_SPOT_OPEN, NIFTY_EXPIRY, access_token, option_lookup, today)

    print("[STEP 4] Scanning SENSEX...")
    sensex_results = scan_index("SENSEX", SENSEX_SPOT_OPEN, SENSEX_EXPIRY, access_token, option_lookup, today)

    print("[STEP 5] Scanning high-volume stocks...")
    stock_results = scan_stocks(access_token, option_lookup, today)

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

    return signals


if __name__ == "__main__":
    main()
