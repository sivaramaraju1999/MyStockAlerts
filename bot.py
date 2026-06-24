#!/usr/bin/env python3
"""
UNIVERSAL LIVE INDIAN MARKET INTELLIGENCE BOT
=============================================
Filename: bot.py
Version: 7.1 (All-Day Live Update Core)
"""

import os
import sys
import time
import requests
from datetime import datetime
import pytz
import yfinance as yf

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# ENVIRONMENT SECRET CONFIGURATION
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

GROQ_TIMEOUT = 180           
GROQ_MAX_RETRIES = 3
TELEGRAM_TIMEOUT = 30


def get_ist_now():
    return datetime.now(IST)


def dbg(msg: str):
    ts = get_ist_now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ─────────────────────────────────────────────
# TELEGRAM DISPATCHER
# ─────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4000
    chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]

    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        for attempt in range(1, 4):
            try:
                r = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
                r.raise_for_status()
                break
            except Exception as e:
                dbg(f"[WARN] Telegram push failed: {e}")
                time.sleep(2)


# ─────────────────────────────────────────────
# GROQ TRANSFORM TRANSCRIPTER ENGINE
# ─────────────────────────────────────────────
def call_groq(prompt: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {
                "role": "system",
                "content": "You are an elite Indian market reporting engine. Format the user data cleanly using professional markdown structures. All ticker assets tagged inside 'DYNAMIC_LOSERS' must strictly render inside the '📉 BOTTOM PERFORMERS TODAY' list. Do not let text fields mix up headings."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.0,
        "max_tokens": 4096,
    }

    for attempt in range(1, GROQ_MAX_RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=GROQ_TIMEOUT)
            r.raise_for_status()
            return r.json().get("choices", [])[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            dbg(f"[WARN] Groq validation failed: {e}")
            time.sleep(5)
    return "[ERROR] LLM mapping engine timed out."


# ─────────────────────────────────────────────
# PROMPT FORMAT LAYOUT DESIGNER
# ─────────────────────────────────────────────
def build_prompt(report_type: str, data_payload: dict) -> str:
    now = get_ist_now()
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M %p IST")

    # Dynamic status tag based on the runtime arg
    status_mapping = {
        "morning": "PRE-MARKET OPEN ANALYSIS",
        "midday": "LIVE MIDDAY SNAPSHOT",
        "evening": "MARKET CLOSE SUMMARY"
    }
    status_header = status_mapping.get(report_type.lower(), f"{report_type.upper()} UPDATE")

    return f"""
    Today is {date_str}, {time_str}. The current run window is: {status_header}.
    
    INJECTED STRUCTURAL METRICS DATA:
    • Broad Index Performance: {data_payload.get('index_metrics', '')}
    • Leading Sector Inflow: {data_payload.get('sector_gainers', '')}
    • Lagging Sector Outflow: {data_payload.get('sector_losers', '')}
    • DYNAMIC_GAINERS: {data_payload.get('stock_gainers_string', '')}
    • DYNAMIC_LOSERS: {data_payload.get('stock_losers_string', '')}
    • Macro Drivers Context: {data_payload.get('macro_triggers', '')}

    MISSION:
    Format a beautiful {status_header} intelligence card. You must keep gainers and losers in their designated blocks. 
    Assets inside 'DYNAMIC_LOSERS' belong exclusively under the '📉 BOTTOM PERFORMERS TODAY' layout block.

    OUTPUT FORMAT Layout:

    🏁 *{status_header} — {date_str}*
    _{time_str}_

    ━━━━━━━━━━━━━━━━━━━━
    📊 *CURRENT SCORECARD (NSE/BSE)*
    ━━━━━━━━━━━━━━━━━━━━
    {data_payload.get('index_metrics', '')}
    • Top Sector Outperformance: {data_payload.get('sector_gainers', '')}
    • Worst Sector Profit Booking: {data_payload.get('sector_losers', '')}

    ━━━━━━━━━━━━━━━━━━━━
    🏆 *TOP PERFORMERS TODAY (NSE)*
    ━━━━━━━━━━━━━━━━━━━━
    {data_payload.get('stock_gainers_string', '')}

    ━━━━━━━━━━━━━━━━━━━━
    📉 *BOTTOM PERFORMERS TODAY (NSE)*
    ━━━━━━━━━━━━━━━━━━━━
    {data_payload.get('stock_losers_string', '')}

    ━━━━━━━━━━━━━━━━━━━━
    📈 *STRATEGIC MARKET PREP*
    ━━━━━━━━━━━━━━━━━━━━
    • Macro Overview: {data_payload.get('macro_triggers', '')}
    • Groww Operational Rule: To bypass automated intraday MIS account square-off fees, ensure all active intraday execution entries are closed manually prior to 3:00 PM IST directly via your terminal.

    _Review notes. Manage orders manually on your Groww terminal._
    """


# ─────────────────────────────────────────────
# DATA SCRAPER INTEGRATION LAYER
# ─────────────────────────────────────────────
def run_automated_pipeline(report_type: str):
    dbg(f"Initiating automated pipeline for: {report_type}")
    
    # 1. SCRAPE LIVE INDIAN INDICES FROM FREE YAHOO DATA
    scraped_indices = {}
    indices_map = {"^NSEI": "Nifty 50", "^BSESN": "BSE Sensex"}
    
    for ticker_id, clean_name in indices_map.items():
        try:
            ticker_obj = yf.Ticker(ticker_id)
            df = ticker_obj.history(period="1d")
            if not df.empty:
                close_p = df['Close'].iloc[-1]
                open_p = df['Open'].iloc[0]
                pct_change = ((close_p - open_p) / open_p) * 100
                scraped_indices[clean_name] = f"**{close_p:,.2f}** ({pct_change:+.2f}%)"
            else:
                scraped_indices[clean_name] = "Data Unavailable"
        except Exception:
            scraped_indices[clean_name] = "Fetch Timeout"

    # 2. RUN DYNAMIC STOCK TRACKING SNAPSHOT
    dynamic_watchlist = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "CIPLA", "DRREDDY", "TATASTEEL", "SBIN", "AXISBANK"]
    yf_tickers = [f"{sym}.NS" for sym in dynamic_watchlist]
    
    gainers_lines = []
    losers_lines = []

    try:
        raw_data = yf.download(yf_tickers, period="1d", group_by="ticker", progress=False)
        processed_pool = []
        
        for sym in dynamic_watchlist:
            yf_sym = f"{sym}.NS"
            if yf_sym in raw_data.columns.levels[0]:
                stock_df = raw_data[yf_sym]
                if not stock_df.empty and 'Close' in stock_df:
                    close_val = stock_df['Close'].iloc[-1]
                    open_val = stock_df['Open'].iloc[0]
                    if open_val > 0:
                        change_pct = ((close_val - open_val) / open_val) * 100
                        processed_pool.append({"symbol": sym, "change": change_pct, "ltp": close_val})

        # Sort dynamically by mathematical move percentage
        sorted_pool = sorted(processed_pool, key=lambda x: x['change'], reverse=True)
        
        for asset in sorted_pool:
            sym = asset['symbol']
            change = asset['change']
            price = asset['ltp']
            url = f"https://groww.in/search?q={sym}"
            line = f"• {sym}: {change:+.2f}% to Rs {price:,.2f} → [Trade on Groww]({url})"
            
            if change >= 0:
                gainers_lines.append(line)
            else:
                losers_lines.append(line)

    except Exception as e:
        dbg(f"Bulk data collection error: {e}")

    # 3. CONSOLIDATE PAYLOAD
    live_payload = {
        "index_metrics": f"• Nifty 50: {scraped_indices.get('Nifty 50', 'N/A')}\n• BSE Sensex: {scraped_indices.get('BSE Sensex', 'N/A')}",
        "sector_gainers": "Pharma / Defensives" if losers_lines else "Broad Market Inflow",
        "sector_losers": "High-Beta Growth Blocks" if losers_lines else "Omitted",
        "stock_gainers_string": "\n".join(gainers_lines) if gainers_lines else "No dynamic gainers recorded.",
        "stock_losers_string": "\n".join(losers_lines) if losers_lines else "No dynamic laggards recorded.",
        "macro_triggers": f"Automated cross-asset liquidity tracking for {report_type.upper()} update vectors."
    }

    # Build prompt dynamically using the run execution type
    prompt = build_prompt(report_type, data_payload=live_payload)
    result = call_groq(prompt)
    send_telegram(result)


if __name__ == "__main__":
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GROQ_API_KEY]):
        print("[CRITICAL] Missing core environment variables.")
        sys.exit(1)

    # Defaults to 'morning' if no argument is explicitly provided, 
    # but any string passed will now process fully.
    target_report = sys.argv[1].lower() if len(sys.argv) > 1 else "morning"
    run_automated_pipeline(target_report)
