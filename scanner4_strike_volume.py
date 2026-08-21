"""
Scanner 4 - Strike Selection + High Volume Stock Filter
----------------------------------------------------------
1. get_strike_count()      -> Index-oda (NIFTY/SENSEX) trading-day-count based
                               reducing strike pattern kudukkum
2. get_atm_strikes()       -> Spot opening price vase, mela/keezha OTM strikes
                               list pannum (CE + PE)
3. get_high_volume_stocks()-> F&O stock list-la irundhu, munnadi naal volume
                               vachu top N stocks filter pannum

NOTE: Upstox API auth (access_token) Scanner 2-la already build panniachu.
      Adha reuse pannunga -> from scanner2_auth import get_access_token
      Idhu file-la, access_token oru function argument-a mattum eduthukurom.
"""

import requests
import datetime
from typing import List, Dict


# ============================================================
# 1. STRIKE COUNT PATTERN (post-expiry reducing logic)
# ============================================================

# Trading-day-count (1 = first trading day after expiry) -> strike count each side
STRIKE_PATTERN = {
    "NIFTY":   {1: 5, 2: 4, 3: 3, 4: 2, 5: 2},
    "SENSEX":  {1: 10, 2: 8, 3: 6, 4: 4, 5: 4},
}

# Weekly expiry weekday: NIFTY -> Tuesday(1), SENSEX -> Thursday(3)
EXPIRY_WEEKDAY = {
    "NIFTY": 1,   # Monday=0 ... Tuesday=1
    "SENSEX": 3,  # Thursday=3
}


def get_trading_day_count_since_expiry(index_name: str, trading_days_calendar: List[datetime.date],
                                        today: datetime.date) -> int:
    """
    trading_days_calendar: sorted list of actual trading dates (holidays already removed)
    Returns: count of trading days since the LAST expiry (1-indexed).
             If count > 5, returns 5 (pattern stays flat at day-5 value till next expiry).
    """
    # find last expiry date <= today, based on expiry weekday
    last_expiry = None
    for d in reversed(trading_days_calendar):
        if d > today:
            continue
        if d.weekday() == EXPIRY_WEEKDAY[index_name]:
            last_expiry = d
            break

    if last_expiry is None:
        raise ValueError(f"Could not resolve last expiry for {index_name} before {today}")

    # count trading days strictly after last_expiry, up to and including today
    days_after = [d for d in trading_days_calendar if last_expiry < d <= today]
    count = len(days_after)
    return min(count, 5) if count > 0 else 1  # expiry day itself -> treat as day 1 of new cycle


def get_strike_count(index_name: str, trading_days_calendar: List[datetime.date],
                      today: datetime.date = None) -> int:
    """Public function: returns OTM strike count (each side) for today."""
    if index_name not in STRIKE_PATTERN:
        raise ValueError(f"No strike pattern defined for {index_name}")
    today = today or datetime.date.today()
    day_count = get_trading_day_count_since_expiry(index_name, trading_days_calendar, today)
    return STRIKE_PATTERN[index_name][day_count]


# ============================================================
# 2. STRIKE LIST BUILDER (index + stock)
# ============================================================

def round_to_strike_step(price: float, step: int) -> float:
    """Rounds price to nearest strike step (NIFTY=50, SENSEX=100, stocks vary)."""
    return round(price / step) * step


def get_atm_strikes(spot_open_price: float, step: int, otm_count: int) -> Dict[str, List[float]]:
    """
    Given today's opening spot price, returns OTM CE and PE strike lists.
    CE strikes -> ABOVE spot (OTM calls)
    PE strikes -> BELOW spot (OTM puts)
    """
    atm = round_to_strike_step(spot_open_price, step)
    ce_strikes = [atm + step * i for i in range(1, otm_count + 1)]
    pe_strikes = [atm - step * i for i in range(1, otm_count + 1)]
    return {"CE": ce_strikes, "PE": pe_strikes}


def build_index_strike_list(index_name: str, spot_open_price: float,
                             trading_days_calendar: List[datetime.date],
                             today: datetime.date = None) -> Dict[str, List[float]]:
    """One-shot function: index name + today's spot open -> final CE/PE strike list."""
    step = 50 if index_name == "NIFTY" else 100  # SENSEX step = 100
    otm_count = get_strike_count(index_name, trading_days_calendar, today)
    return get_atm_strikes(spot_open_price, step, otm_count)


def build_stock_strike_list(spot_open_price: float, strike_step: int) -> Dict[str, List[float]]:
    """Stocks -> fixed 5 OTM each side."""
    return get_atm_strikes(spot_open_price, strike_step, otm_count=5)


# ============================================================
# 3. HIGH VOLUME STOCK FILTER
# ============================================================

# Upstox instrument endpoint for F&O stock master (adjust if your Scanner2/3 already
# has a cached instrument list -> reuse that file instead of hitting API again)
UPSTOX_MARKET_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"


def get_fo_stock_universe(csv_path: str = "fo_stocks.csv") -> List[str]:
    """
    Returns the list of F&O eligible stock trading symbols.
    Reads from fo_stocks.csv (208 symbols, sourced from NSE fo_mktlots.csv,
    Aug 2026 list). Commit fo_stocks.csv to the repo; refresh it monthly
    by re-downloading https://archives.nseindia.com/content/fo/fo_mktlots.csv
    and re-extracting the SYMBOL column.
    """
    import csv
    with open(csv_path, encoding="utf-8-sig") as f:  # utf-8-sig strips BOM if present
        reader = csv.DictReader(f)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]  # strip stray tabs/spaces
        return [row["SYMBOL"].strip() for row in reader]


def get_instrument_key_map(csv_path: str = "fo_instrument_keys.csv") -> Dict[str, str]:
    """
    Loads SYMBOL -> instrument_key mapping (pre-built from Upstox instrument
    master, see build_instrument_key_csv.py). 208/208 F&O symbols matched
    as of Aug 2026 master file - re-run the builder monthly when fo_stocks.csv
    is refreshed, since Upstox instrument_keys can occasionally change.
    """
    import csv
    mapping = {}
    with open(csv_path, encoding="utf-8-sig") as f:  # utf-8-sig strips BOM if present
        reader = csv.DictReader(f)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]  # strip stray tabs/spaces
        for row in reader:
            mapping[row["SYMBOL"].strip()] = row["INSTRUMENT_KEY"].strip()
    return mapping


def get_high_volume_stocks(access_token: str, top_n: int = 15,
                            fo_universe: List[str] = None,
                            instrument_key_csv: str = "fo_instrument_keys.csv") -> List[str]:
    """
    Fetches previous day's traded volume for F&O stocks and returns the
    top_n stocks by volume. Run this ONCE daily (pre-market) and cache
    the result for that day's scan.

    Uses batched calls (Upstox market-quote accepts comma-separated
    instrument_keys in one request, ~500 keys per call limit) instead of
    one API call per stock - much faster and avoids rate limits.
    """
    fo_universe = fo_universe or get_fo_stock_universe()
    key_map = get_instrument_key_map(instrument_key_csv)

    instrument_keys = [key_map[s] for s in fo_universe if s in key_map]
    key_to_symbol = {v: k for k, v in key_map.items()}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    volume_map = {}
    batch_size = 500  # Upstox comma-separated instrument_key limit per call
    for i in range(0, len(instrument_keys), batch_size):
        batch = instrument_keys[i:i + batch_size]
        try:
            resp = requests.get(
                UPSTOX_MARKET_QUOTE_URL,
                headers=headers,
                params={"instrument_key": ",".join(batch)},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            for _, quote in data.items():
                ikey = quote.get("instrument_token") or quote.get("instrument_key")
                symbol = key_to_symbol.get(ikey)
                if symbol:
                    volume_map[symbol] = quote.get("volume", 0)
        except Exception as e:
            print(f"[WARN] batch volume fetch failed (batch {i // batch_size}): {e}")
            continue

    sorted_stocks = sorted(volume_map.items(), key=lambda x: x[1], reverse=True)
    top_stocks = [s for s, v in sorted_stocks[:top_n]]
    return top_stocks


# ============================================================
# QUICK TEST (run standalone to sanity check strike pattern)
# ============================================================
if __name__ == "__main__":
    # dummy trading calendar for testing (replace with real NSE trading calendar)
    cal = [datetime.date(2026, 8, d) for d in range(1, 32) if datetime.date(2026, 8, d).weekday() < 5]

    for idx in ["NIFTY", "SENSEX"]:
        for test_date in cal[-6:]:
            n = get_strike_count(idx, cal, test_date)
            print(f"{idx} | {test_date} ({test_date.strftime('%a')}) -> {n} strikes each side")

    print("\nSample strike build (NIFTY, spot=24837):")
    print(build_index_strike_list("NIFTY", 24837, cal))

    print("\nSample stock strike build (fixed 5 OTM, spot=1523, step=20):")
    print(build_stock_strike_list(1523, 20))
