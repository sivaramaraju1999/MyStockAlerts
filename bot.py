#!/usr/bin/env python3
"""
UNIVERSAL MARKET INTELLIGENCE ENGINE (PURE GENERIC VARIABLE ARCHITECTURE)
========================================================================
Version: 6.5 (Absolute Zero Hardcoding)
Last Updated: 2026-06-23

Key Features:
1. Zero Stock Reference: No tickers are hardcoded anywhere in the logic.
2. Direct Groww URL Injector: Automatically compiles trading URLs for any active asset.
3. Isolated Buckets: Separates data inputs into rigid variables so the AI cannot cross-contaminate lists.
"""

import os
import sys
import time
import requests
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# CONFIGURATION CONSTANTS
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
# OUTBOUND TELEGRAM TELEMETRY
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
                dbg(f"[WARN] Telegram retry failed: {e}")
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
                "content": "You are a professional market data layout engine. Your job is to format the user's input arrays into clean markdown. Data marked as DYNAMIC_GAINERS must be placed in the TOP PERFORMERS section. Data marked as DYNAMIC_LOSERS must be placed strictly in the BOTTOM PERFORMERS section. Do not alter the raw mathematical percentages or cross-contaminate data blocks."
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
            dbg(f"[WARN] Groq mapping attempt {attempt} failed: {e}")
            time.sleep(5)
    return "[ERROR] Processing engine timed out."


# ─────────────────────────────────────────────
# PROMPT FORMAT ENGINE
# ─────────────────────────────────────────────
def build_prompt(report_type: str, data_payload: dict) -> str:
    now = get_ist_now()
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M %p IST")

    if report_type == "evening":
        return f"""
        Today is {date_str}, {time_str}. The market is CLOSED.
        
        INBOUND DYNAMIC DATA ARRAYS:
        • Broad Index Performance: {data_payload.get('index_metrics', '')}
        • Sector Gainers: {data_payload.get('sector_gainers', '')}
        • Sector Losers: {data_payload.get('sector_losers', '')}
        • DYNAMIC_GAINERS: {data_payload.get('stock_gainers_string', '')}
        • DYNAMIC_LOSERS: {data_payload.get('stock_losers_string', '')}
        • Underlying Macro Catalysts: {data_payload.get('macro_triggers', '')}

        MISSION:
        Format an End-of-Day scorecard based precisely on the data structures passed above. 
        You must ensure that data under 'DYNAMIC_LOSERS' maps strictly inside the '📉 BOTTOM PERFORMERS TODAY' layout block.

        OUTPUT FORMAT:

        🏁 *MARKET CLOSE & TOMORROW'S PREP — {date_str}*
        _{time_str}_

        ━━━━━━━━━━━━━━━━━━━━
        📊 *TODAY'S SCORECARD (NSE/BSE)*
        ━━━━━━━━━━━━━━━━━━━━
        {data_payload.get('index_metrics', '')}
        • Leading Sector Money Flow: {data_payload.get('sector_gainers', '')}
        • Sector Capital Outflows: {data_payload.get('sector_losers', '')}

        ━━━━━━━━━━━━━━━━━━━━
        🏆 *TOP PERFORMERS TODAY (NSE)*
        ━━━━━━━━━━━━━━━━━━━━
        {data_payload.get('stock_gainers_string', '')}

        ━━━━━━━━━━━━━━━━━━━━
        📉 *BOTTOM PERFORMERS TODAY (NSE)*
        ━━━━━━━━━━━━━━━━━━━━
        {data_payload.get('stock_losers_string', '')}

        ━━━━━━━━━━━━━━━━━━━━
        📈 *TOMORROW'S PRE-MARKET PREP (9:15 AM IST)*
        ━━━━━━━━━━━━━━━━━━━━
        • Technical Indicators: {data_payload.get('macro_triggers', '')}
        • Groww Execution Rule: To bypass automated intraday MIS account square-off platform fees, ensure all manual open positions are exited on your terminal before 3:00 PM IST.

        _Review notes. Prepare orders manually on your Groww panel for tomorrow._
        """
    return ""


# ─────────────────────────────────────────────
# UNIVERSAL REPORT CONTROLLER
# ─────────────────────────────────────────────
def run_evening_report(scraped_indices: dict, day_gainers: list, day_losers: list, macro_text: str):
    dbg("=== RUNNING POST-MARKET COMPILER ENGINE ===")
    
    # Process dynamic gainers list on the fly without looking for hardcoded names
    gainers_lines = []
    for asset in day_gainers:
        sym = asset['symbol'].upper()
        change = asset['change']
        price = asset['ltp']
        url = f"https://groww.in/search?q={sym}"
        gainers_lines.append(f"• {sym}: {change:+.2f}% to Rs {price:,.2f} → [Trade on Groww]({url})")
    
    # Process dynamic losers list on the fly without looking for hardcoded names
    losers_lines = []
    for asset in day_losers:
        sym = asset['symbol'].upper()
        change = asset['change']
        price = asset['ltp']
        url = f"https://groww.in/search?q={sym}"
        losers_lines.append(f"• {sym}: {change:+.2f}% to Rs {price:,.2f} → [Trade on Groww]({url})")

    # Combine data structures cleanly
    live_payload = {
        "index_metrics": f"• Nifty 50: {scraped_indices.get('index1_close')} ({scraped_indices.get('index1_pct')})\n• BSE Sensex: {scraped_indices.get('index2_close')} ({scraped_indices.get('index2_pct')})",
        "sector_gainers": scraped_indices.get("top_sector", "None"),
        "sector_losers": scraped_indices.get("worst_sector", "None"),
        "stock_gainers_string": "\n".join(gainers_lines) if gainers_lines else "No major outperformance logged.",
        "stock_losers_string": "\n".join(losers_lines) if losers_lines else "No major laggards logged.",
        "macro_triggers": macro_text
    }

    prompt = build_prompt("evening", data_payload=live_payload)
    result = call_groq(prompt)
    send_telegram(result)
    dbg("=== REPORT PROCESSING TERMINATED SUCCESSFULLY ===")


# ─────────────────────────────────────────────
# CONTROL ROUTER
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "evening":
        # ⚠️ THIS IS AN EMPTY ADAPTIVE SHELL.
        # You link this section straight to your system's dynamic database output variables.
        # Example dictionary names are intentionally generic placeholders.
        
        dynamic_indices = {
            "index1_close": "0.00", "index1_pct": "0.00%",
            "index2_close": "0.00", "index2_pct": "0.00%",
            "top_sector": "Variable_Data",
            "worst_sector": "Variable_Data"
        }
        
        dynamic_gainers = []  # Injected arrays are completely empty by default
        dynamic_losers = []   # Injected arrays are completely empty by default
        dynamic_macro = "System tracking parameters go here."

        run_evening_report(dynamic_indices, dynamic_gainers, dynamic_losers, dynamic_macro)
    else:
        print("Usage: python bot.py evening")
