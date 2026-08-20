"""
Upstox Manual Daily Login
--------------------------
Run this ONCE every trading day (before market open) to get a fresh
access_token. Token is valid till end of day (Upstox tokens expire daily
at ~3:30 AM IST regardless of when generated).

Usage:
    python scanner4_manual_login.py

Flow:
    1. Script prints an authorization URL
    2. Open it in browser, login with your Upstox credentials + TOTP (manual)
    3. After login, browser redirects to https://127.0.0.1/?code=XXXXX
       (page will show "can't be reached" - that's fine, just copy the URL)
    4. Paste the full redirected URL (or just the code) back into the script
    5. Script exchanges the code for access_token and saves it to token.txt

API_KEY / API_SECRET should come from GitHub Secrets in the actual
GitHub Actions run - for local/manual testing, set them as environment
variables or paste directly (NEVER commit them to the repo).
"""

import os
import re
import requests
from urllib.parse import urlencode

# ---- Config: pull from environment (set these in your shell / GitHub Secrets) ----
API_KEY = os.environ.get("UPSTOX_API_KEY", "")
API_SECRET = os.environ.get("UPSTOX_API_SECRET", "")
REDIRECT_URI = "https://127.0.0.1"

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"

TOKEN_FILE = "token.txt"


def get_authorization_url() -> str:
    params = {
        "client_id": API_KEY,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def extract_code(pasted_input: str) -> str:
    """Accepts either the full redirected URL or just the raw code."""
    match = re.search(r"[?&]code=([^&\s]+)", pasted_input)
    if match:
        return match.group(1)
    return pasted_input.strip()  # assume they pasted just the code


def exchange_code_for_token(code: str) -> str:
    payload = {
        "code": code,
        "client_id": API_KEY,
        "client_secret": API_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"]


def save_token(token: str, path: str = TOKEN_FILE):
    with open(path, "w") as f:
        f.write(token.strip())
    print(f"[OK] access_token saved to {path}")


def load_token(path: str = TOKEN_FILE) -> str:
    """Other scanner scripts call this to read today's saved token."""
    with open(path) as f:
        return f.read().strip()


if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        print("[ERROR] Set UPSTOX_API_KEY and UPSTOX_API_SECRET as environment variables first.")
        print('  export UPSTOX_API_KEY="your_key"')
        print('  export UPSTOX_API_SECRET="your_secret"')
        exit(1)

    print("\nStep 1: Open this URL in your browser and login manually (with TOTP):\n")
    print(get_authorization_url())
    print("\nStep 2: After login, browser will redirect to a 127.0.0.1 URL that fails to load.")
    print("        Copy that FULL URL from the address bar (it has ?code=... in it).\n")

    pasted = input("Paste the redirected URL (or just the code) here: ").strip()
    code = extract_code(pasted)

    try:
        token = exchange_code_for_token(code)
        save_token(token)
        print("\n[DONE] Today's access_token is ready. Other scanner scripts can now run.")
    except Exception as e:
        print(f"[ERROR] Token exchange failed: {e}")
