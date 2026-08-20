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
    """
    with open(master_json_path) as f:
        data = json.load(f)

    lookup = {}
    for d in data:
        if d.get("instrument_type") not in ("CE", "PE"):
            continue
        name = d.get("name")
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


def fetch_daily_ohlc(access_token: str, instrument_key: str,
                      target_date: datetime.date) -> Optional[OHLC]:
    """Fetches OHLC for a SINGLE previous trading day (target_date)."""
    date_str = target_date.strftime("%Y-%m-%d")
    candles = fetch_daily_candles(access_token, instrument_key, date_str, date_str)
    if not candles:
        return None
    c = candles[0]  # [timestamp, open, high, low, close, volume, oi]
    return OHLC(high=c[2], low=c[3], close=c[4])


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
    return OHLC(high=max(highs), low=min(lows), close=last_close)


# ============================================================
# QUICK TEST (structure check only - needs live access_token to actually run)
# ============================================================
if __name__ == "__main__":
    print("This module needs a live access_token + today's instrument master to test fully.")
    print("Run download_instrument_master() first, then build_option_lookup(),")
    print("then get_option_instrument_key() to resolve a strike's instrument_key,")
    print("then fetch_daily_ohlc() / fetch_weekly_ohlc() using your token.txt token.")
