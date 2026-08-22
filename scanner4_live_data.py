"""
Scanner 4 - Live Spot Price + Dynamic Expiry Date
-----------------------------------------------------
Replaces the hardcoded NIFTY_SPOT_OPEN, SENSEX_SPOT_OPEN, and expiry date
constants in scanner4_main.py with live values fetched each run.

1. get_index_spot_open()   -> today's opening price for NIFTY / SENSEX
2. get_all_stock_opens()   -> today's opening price for a batch of stocks
3. get_next_weekly_expiry()-> next NIFTY (Tue) / SENSEX (Thu) expiry, DD-MM-YYYY
4. get_next_monthly_expiry()-> last trading day of the current/next month
                               (for monthly stock options)
"""

import datetime
import requests

MARKET_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"

INDEX_INSTRUMENT_KEY = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "SENSEX": "BSE_INDEX|SENSEX",
}

EXPIRY_WEEKDAY = {
    "NIFTY": 1,   # Tuesday
    "SENSEX": 3,  # Thursday
}


# ============================================================
# 1. LIVE SPOT OPEN PRICE
# ============================================================

def _fetch_quote(access_token: str, instrument_key: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    resp = requests.get(MARKET_QUOTE_URL, headers=headers,
                         params={"instrument_key": instrument_key}, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if not data:
        raise ValueError(f"No quote data returned for {instrument_key}")
    return list(data.values())[0]  # single instrument requested -> single result


def get_index_spot_open(access_token: str, index_name: str) -> float:
    """
    index_name: 'NIFTY' or 'SENSEX'
    Returns today's opening price. Raises if market hasn't opened yet
    (ohlc.open will be 0 or missing pre-market) - run this AFTER 9:15 AM IST.
    """
    ikey = INDEX_INSTRUMENT_KEY[index_name]
    quote = _fetch_quote(access_token, ikey)
    open_price = quote.get("ohlc", {}).get("open")
    if not open_price:
        raise ValueError(f"{index_name} opening price not yet available - "
                          f"has the market opened? (run after 9:15 AM IST)")
    return float(open_price)


def get_all_stock_opens(access_token: str, symbols: list, key_map: dict) -> dict:
    """
    Batch version - fetches opening price for multiple stocks in one call
    (comma-separated instrument_keys, same batching approach as volume fetch).
    Returns {symbol: open_price}.
    """
    instrument_keys = [key_map[s] for s in symbols if s in key_map]
    key_to_symbol = {v: k for k, v in key_map.items()}

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    opens = {}
    batch_size = 500
    for i in range(0, len(instrument_keys), batch_size):
        batch = instrument_keys[i:i + batch_size]
        resp = requests.get(MARKET_QUOTE_URL, headers=headers,
                             params={"instrument_key": ",".join(batch)}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        for _, quote in data.items():
            ikey = quote.get("instrument_token") or quote.get("instrument_key")
            symbol = key_to_symbol.get(ikey)
            open_price = quote.get("ohlc", {}).get("open")
            if symbol and open_price:
                opens[symbol] = float(open_price)
    return opens


# ============================================================
# 2. DYNAMIC EXPIRY DATE CALCULATION
# ============================================================

def get_next_weekly_expiry(index_name: str, today: datetime.date = None) -> str:
    """
    Returns the next upcoming weekly expiry date for NIFTY (Tuesday) or
    SENSEX (Thursday), in 'DD-MM-YYYY' format (matches Upstox instrument
    master's expiry format).

    If today IS the expiry weekday, returns today's date (expiry day itself
    is still valid for that week's contract until market close).
    """
    today = today or datetime.date.today()
    target_weekday = EXPIRY_WEEKDAY[index_name]
    days_ahead = (target_weekday - today.weekday()) % 7
    expiry_date = today + datetime.timedelta(days=days_ahead)
    return expiry_date.strftime("%d-%m-%Y")


def get_next_monthly_expiry(today: datetime.date = None) -> str:
    """
    Returns the last trading day of the current month (or next month if
    already past it) as an approximation of monthly F&O expiry.

    NOTE: This is a simple 'last day of month' approximation. Real NSE
    monthly expiry is the LAST TRADING DAY (accounting for holidays),
    which may differ by 1-2 days around month-end holidays. For production
    accuracy, cross-check against NSE's official expiry calendar.
    """
    today = today or datetime.date.today()

    if today.month == 12:
        next_month_first = datetime.date(today.year + 1, 1, 1)
    else:
        next_month_first = datetime.date(today.year, today.month + 1, 1)
    last_day_this_month = next_month_first - datetime.timedelta(days=1)

    while last_day_this_month.weekday() >= 5:
        last_day_this_month -= datetime.timedelta(days=1)

    if today <= last_day_this_month:
        return last_day_this_month.strftime("%d-%m-%Y")

    if next_month_first.month == 12:
        month_after_first = datetime.date(next_month_first.year + 1, 1, 1)
    else:
        month_after_first = datetime.date(next_month_first.year, next_month_first.month + 1, 1)
    last_day_next_month = month_after_first - datetime.timedelta(days=1)
    while last_day_next_month.weekday() >= 5:
        last_day_next_month -= datetime.timedelta(days=1)
    return last_day_next_month.strftime("%d-%m-%Y")


# ============================================================
# QUICK TEST (date logic only, no live token needed for this part)
# ============================================================
if __name__ == "__main__":
    test_dates = [
        datetime.date(2026, 8, 24),  # Monday
        datetime.date(2026, 8, 25),  # Tuesday (NIFTY expiry day)
        datetime.date(2026, 8, 27),  # Thursday (SENSEX expiry day)
        datetime.date(2026, 8, 31),  # month end
    ]
    for d in test_dates:
        print(f"{d} ({d.strftime('%a')}): "
              f"NIFTY expiry={get_next_weekly_expiry('NIFTY', d)} | "
              f"SENSEX expiry={get_next_weekly_expiry('SENSEX', d)} | "
              f"Monthly expiry={get_next_monthly_expiry(d)}")
