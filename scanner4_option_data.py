"""
Scanner 4 - Option Instrument Lookup + OHLC Fetch
----------------------------------------------------
1. build_option_lookup()  -> loads Upstox instrument master once, builds a
                              fast lookup: (name, strike, CE/PE, expiry) -> instrument_key
2. get_option_instrument_key() -> finds instrument_key for a specific strike
3. fetch_daily_ohlc()      -> previous day's OHLC for an option (from Upstox
                              historical candle API)
4. fetch_weekly_ohlc()     -> previous week's OHLC (aggregated from daily candles)

SEGMENT MAPPING:
    NIFTY, BANKNIFTY, FINNIFTY -> NSE_FO
    SENSEX                     -> BSE_FO
    Stocks                     -> NSE_FO (name = stock symbol)
"""

import json
import gzip
import requests
import datetime
from typing import Optional, List
from scanner4_cpr_calculator import OHLC

INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
HISTORICAL_CANDLE_URL = "https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"

INDEX_SEGMENT = {
    "NIFTY": "NSE_FO",
    "BANKNIFTY": "NSE_FO",
    "FINNIFTY": "NSE_FO",
    "SENSEX": "BSE_FO",
}


# ============================================================
# 1. INSTRUMENT MASTER DOWNLOAD + OPTION LOOKUP BUILD
# ============================================================

def download_instrument_master(save_path: str = "complete.json") -> str:
    """
    Downloads and unzips Upstox's daily instrument master file.
    Run this ONCE per day (pre-market), before building the option lookup.
    """
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    raw = gzip.decompress(resp.content)
    with open(save_path, "wb") as f:
        f.write(raw)
    print(f"[OK] instrument master saved to {save_path}")
    return save_path


def build_option_lookup(master_json_path: str = "complete.json") -> dict:
    """
    Builds lookup dict:
        key   = (name, strike_price, instrument_type, expiry_date_str "DD-MM-YYYY")
        value = instrument_key

    Only keeps CE/PE rows to keep this fast and light in memory.

    Uses 'underlying_symbol' (e.g. 'TATASTEEL') for the name, NOT the raw
    'name' field - for stock options, Upstox's 'name' field is the full
    company name (e.g. 'TATA STEEL LIMITED'), which won't match trading
    symbols from fo_stocks.csv. For indices, underlying_symbol still
    resolves correctly (falls back to 'name' if underlying_symbol is
    missing, which covers NIFTY/SENSEX index option rows).
    """
    with open(master_json_path) as f:
        data = json.load(f)

    lookup = {}
    for d in data:
        if d.get("instrument_type") not in ("CE", "PE"):
            continue
        name = d.get("underlying_symbol") or d.get("name")
        strike = d.get("strike_price")
        opt_type = d.get("instrument_type")
        expiry_ms = d.get("expiry")
        if not (name and strike and expiry_ms):
            continue
        expiry_date = datetime.datetime.utcfromtimestamp(expiry_ms / 1000).strftime("%d-%m-%Y")
        key = (name, float(strike), opt_type, expiry_date)
        lookup[key] = d["instrument_key"]

    print(f"[OK] option lookup built: {len(lookup)} contracts indexed")
    return lookup


def get_option_instrument_key(lookup: dict, name: str, strike: float,
                               option_type: str, expiry_date: str) -> Optional[str]:
    """
    name: 'NIFTY' / 'SENSEX' / stock symbol e.g. 'RELIANCE'
    strike: strike price (float)
    option_type: 'CE' or 'PE'
    expiry_date: 'DD-MM-YYYY' string matching the contract's expiry
    """
    key = (name, float(strike), option_type, expiry_date)
    return lookup.get(key)


def get_dynamic_strike_step(lookup: dict, name: str, expiry_date: str) -> Optional[float]:
    """
    Derives the real strike step for a symbol+expiry directly from the
    instrument master, instead of relying on a manually maintained
    override table (which goes stale and silently breaks strike lookups
    for any stock not in the table).

    Looks at all CE strikes listed for this symbol+expiry, sorts them,
    and returns the smallest gap between consecutive strikes - this is
    the actual exchange-defined strike step.

    Returns None if fewer than 2 strikes found (can't determine a step).
    """
    strikes = sorted({
        k[1] for k in lookup
        if k[0] == name and k[2] == "CE" and k[3] == expiry_date
    })
    if len(strikes) < 2:
        return None
    gaps = [round(strikes[i + 1] - strikes[i], 4) for i in range(len(strikes) - 1)]
    return min(gaps)


def get_next_expiry_for_symbol(lookup: dict, name: str, today: datetime.date) -> Optional[str]:
    """
    Finds the nearest upcoming expiry date actually listed in the
    instrument master for this symbol - avoids guessing expiry dates
    from calendar rules (which change over time and vary by exchange/
    symbol). Returns 'DD-MM-YYYY' string, or None if no contracts found
    for this symbol at all.
    """
    expiry_dates = set()
    for k in lookup:
        if k[0] == name and k[2] == "CE":
            expiry_dates.add(k[3])

    if not expiry_dates:
        return None

    parsed = []
    for ds in expiry_dates:
        try:
            d = datetime.datetime.strptime(ds, "%d-%m-%Y").date()
        except ValueError:
            continue
        if d >= today:
            parsed.append((d, ds))

    if not parsed:
        return None
    parsed.sort()
    return parsed[0][1]


# ============================================================
# 2. HISTORICAL OHLC FETCH (daily candles from Upstox)
# ============================================================

def fetch_daily_candles(access_token: str, instrument_key: str,
                         from_date: str, to_date: str) -> List[dict]:
    """
    from_date/to_date: 'YYYY-MM-DD' strings
    Returns list of candles: [timestamp, open, high, low, close, volume, oi]
    (Upstox returns most-recent-first; we reverse to chronological order.)
    """
    url = HISTORICAL_CANDLE_URL.format(
        instrument_key=instrument_key, to_date=to_date, from_date=from_date
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    candles = resp.json().get("data", {}).get("candles", [])
    return list(reversed(candles))  # oldest -> newest


def fetch_volume_stats(access_token: str, instrument_key: str,
                        prev_day: datetime.date, lookback_days: int = 20) -> Optional[dict]:
    """
    Fetches the last `lookback_days` trading days of daily candles and
    returns {'current': latest day's volume, 'avg': average volume over
    the lookback window}. Used to classify today's volume as Low/Mid/
    High/Very High relative to its own recent history.

    Fetches a wider calendar window (lookback_days * 2) to safely cover
    weekends/holidays, then keeps only the most recent `lookback_days`
    trading candles.
    """
    from_date = (prev_day - datetime.timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    to_date = prev_day.strftime("%Y-%m-%d")
    candles = fetch_daily_candles(access_token, instrument_key, from_date, to_date)
    if not candles:
        return None
    volumes = [c[5] for c in candles][-lookback_days:]
    if not volumes:
        return None
    return {"current": volumes[-1], "avg": sum(volumes) / len(volumes)}
def fetch_daily_ohlc(access_token: str, instrument_key: str,
                      target_date: datetime.date) -> Optional[OHLC]:
    """Fetches OHLC for a SINGLE previous trading day (target_date)."""
    date_str = target_date.strftime("%Y-%m-%d")
    candles = fetch_daily_candles(access_token, instrument_key, date_str, date_str)
    if not candles:
        return None
    c = candles[0]  # [timestamp, open, high, low, close, volume, oi]
    return OHLC(high=c[2], low=c[3], close=c[4], open=c[1], volume=c[5])


def fetch_weekly_ohlc(access_token: str, instrument_key: str,
                       week_start: datetime.date, week_end: datetime.date) -> Optional[OHLC]:
    """
    Fetches all daily candles for the given week range and aggregates into
    one weekly OHLC (high=max high, low=min low, close=last day's close).
    """
    from_str = week_start.strftime("%Y-%m-%d")
    to_str = week_end.strftime("%Y-%m-%d")
    candles = fetch_daily_candles(access_token, instrument_key, from_str, to_str)
    if not candles:
        return None
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    last_close = candles[-1][4]
    first_open = candles[0][1]
    total_volume = sum(c[5] for c in candles)
    return OHLC(high=max(highs), low=min(lows), close=last_close,
                open=first_open, volume=total_volume)


# ============================================================
# QUICK TEST (structure check only - needs live access_token to actually run)
# ============================================================
if __name__ == "__main__":
    print("This module needs a live access_token + today's instrument master to test fully.")
    print("Run download_instrument_master() first, then build_option_lookup(),")
    print("then get_option_instrument_key() to resolve a strike's instrument_key,")
    print("then fetch_daily_ohlc() / fetch_weekly_ohlc() using your token.txt token.")
