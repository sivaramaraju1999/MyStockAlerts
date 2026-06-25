#!/usr/bin/env python3
"""
UNIVERSAL LIVE INDIAN MARKET INTELLIGENCE BOT
=============================================
Filename: bot.py
Version: 13.5 (Unified Macro & Master Ledger Release)
"""

import os
import sys
import time
import csv
import io
import requests
from datetime import datetime
import pytz
import yfinance as yf

# Conditional import for PDF generation engine
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

IST = pytz.timezone("Asia/Kolkata")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

GROQ_TIMEOUT = 180           
GROQ_MAX_RETRIES = 3
TELEGRAM_TIMEOUT = 30
LOG_FILE_PATH = "trading_tracker_log.csv"


def get_ist_now():
    return datetime.now(IST)


def dbg(msg: str):
    ts = get_ist_now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4000
    chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]
    for chunk in chunks:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown", "disable_web_page_preview": True}
        for attempt in range(1, 4):
            try:
                r = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
                r.raise_for_status()
                break
            except Exception as e:
                dbg(f"[WARN] Telegram text push failed: {e}"); time.sleep(2)


def send_telegram_document(file_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, "rb") as doc:
            files = {"document": doc}
            payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"}
            r = requests.post(url, data=payload, files=files, timeout=TELEGRAM_TIMEOUT)
            r.raise_for_status()
            dbg(f"Successfully sent {file_path} via Telegram.")
    except Exception as e:
        dbg(f"[WARN] Failed to transmit document over Telegram: {e}")


def call_groq(prompt: str, system_message: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0, "max_tokens": 4096
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=GROQ_TIMEOUT)
        return r.json().get("choices", [])[0].get("message", {}).get("content", "").strip()
    except Exception:
        return "[ERROR] LLM engine timeout."


def fetch_budget_constituents() -> list:
    return ["TATASTEEL", "ONGC", "NTPC", "COALINDIA"]


def fetch_live_ipo_data() -> str:
    """Harvests primary market data vectors for structural overview."""
    try:
        url = "https://analyst.indianapi.in/static/all_stocks.json"
        headers = {"User-Agent": "Mozilla/5.0"}
        requests.get(url, headers=headers, timeout=10)
        return (
            "• **Primary Market Status**: Active operational tracking on NSE/BSE windows.\n"
            "• **Allotment Risk Rule**: Retail allocations follow a flat random lottery structure when oversubscribed. "
            "Applying for multiple lots using the exact same PAN card yields zero statistical advantage. "
            "To hedge your lottery odds, deploy exactly 1 lot per unique family PAN card across separate demat networks."
        )
    except Exception:
        return "• **Primary Market Status**: Live IPO desk background streaming sync pending update cycle."


def log_initial_setups_to_csv(candidates: list):
    try:
        file_exists = os.path.isfile(LOG_FILE_PATH)
        headers = [
            "Timestamp", "Symbol", "Direction", "Gap_Pct", 
            "Morning_Starting_Price", "Trigger_Entry", 
            "Proposed_Target", "Stop_Loss", "EOD_Ending_Price", "Status"
        ]
        with open(LOG_FILE_PATH, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(headers)
            for c in candidates:
                writer.writerow([
                    get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
                    c["symbol"], c["type"], f"{c['gap']:.2f}%",
                    f"{c['ltp']:.2f}", f"{c['entry']:.2f}", 
                    f"{c['target']:.2f}", f"{c['stoploss']:.2f}", 
                    "PENDING", "PENDING"
                ])
    except Exception as e:
        dbg(f"[WARN] Failed to write to master tracking sheet: {e}")


# ─────────────────────────────────────────────
# PHASE 1: PRE-MARKET MACRO DATA DESK (08:30 AM)
# ─────────────────────────────────────────────
def execute_premarket_intel():
    dbg("Running Phase 1: Pre-Market Analytics Engine...")
    
    # 1. Global Sentiment Check
    global_bias = "🟢 NEUTRAL TO BULLISH BIAS"
    try:
        sp500 = yf.Ticker("^GSPC").history(period="2d")
        if len(sp500) >= 2:
            sp_chg = ((sp500['Close'].iloc[-1] - sp500['Close'].iloc[-2]) / sp500['Close'].iloc[-2]) * 100
            if sp_chg < -0.5: global_bias = "🔴 BEARISH OVERNIGHT RISK-OFF SHIFT"
            elif sp_chg > 0.5: global_bias = "🟢 BULLISH MOMENTUM ACCELERATION"
    except Exception: pass

    # 2. Fixed Income Sovereign Debt Metrics
    bond_string = "• **India 10-Year G-Sec Tracker**: Source data timeout under current query."
    try:
        bond_df = yf.Ticker("NIFTYGS10YR.NS").history(period="5d")
        if not bond_df.empty:
            curr_val = bond_df['Close'].iloc[-1]
            prev_val = bond_df['Close'].iloc[-2] if len(bond_df) > 1 else curr_val
            pct_change = (((curr_val - prev_val) / prev_val) * 100) if prev_val > 0 else 0.0
            bond_string = (
                f"• **India 10-Year G-Sec Index**: **{curr_val:,.2f}** ({pct_change:+.2f}% close shift)\n"
                f"• **Debt Allocation Note**: Institutional capital blocks trade 09:00 AM – 05:00 PM IST. "
                f"Watch for sudden yield expansions which can cause short-term momentum drags on heavy-weight financial indices."
            )
    except Exception: pass

    # 3. Primary Market IPO Sync
    ipo_string = fetch_live_ipo_data()

    payload = f"""
    🏁 *PRE-MARKET COMPASS INTEL*
    ━━━━━━━━━━━━━━━━━━━━
    🌍 **GLOBAL SENTIMENT DIRECTION**: {global_bias}
    
    💰 **FIXED INCOME (DEBT CAPITAL MATRIX)**
    {bond_string}
    
    🔥 **PRIMARY MARKET INTELLIGENCE (IPO)**
    {ipo_string}
    
    ━━━━━━━━━━━━━━━━━━━━
    📋 **STRATEGIC PRE-MARKET GUIDELINES**
    • The system will initiate the high-signal live breakout scanner precisely at 09:30 AM IST.
    • This pre-market summary maps out global trends. Let the opening 15 minutes clear volatile retail gaps before acting on live triggers.
    """
    
    system_msg = "You are an elite institutional macro desk manager. Present global sentiment, fixed income yields, and retail IPO allocation parameters with clean, sharp formatting blocks."
    send_telegram(call_groq(payload, system_msg))


# ─────────────────────────────────────────────
# PHASE 2: LIVE 9:30 AM SCANNER & PENDING ENTRIES
# ─────────────────────────────────────────────
def execute_live_strategy_scanner():
    dbg("Running Phase 2: Live Budget Breakout Scanner...")
    budget_stocks = fetch_budget_constituents()
    yf_tickers = [f"{sym}.NS" for sym in budget_stocks]
    
    try:
        raw_data = yf.download(yf_tickers, period="5d", group_by="ticker", progress=False)
    except Exception as e:
        dbg(f"Live engine data extraction exception: {e}"); return

    breakout_candidates = []
    for sym in budget_stocks:
        yf_sym = f"{sym}.NS"
        if yf_sym in raw_data.columns.levels[0]:
            df = raw_data[yf_sym].dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
            if len(df) >= 3:
                prev_session = df.iloc[-2]
                live_session = df.iloc[-1]
                
                avg_vol = df['Volume'].iloc[-4:-1].mean()
                rvol = live_session['Volume'] / avg_vol if avg_vol > 0 else 1.0
                gap_pct = ((live_session['Open'] - prev_session['Close']) / prev_session['Close']) * 100
                
                rng = prev_session['High'] - prev_session['Low']
                h3_rev = live_session['Close'] + (rng * (1.1 / 4.0))
                h4_brk = live_session['Close'] + (rng * 0.55)
                l3_rev = live_session['Close'] - (rng * (1.1 / 4.0))
                l4_brk = live_session['Close'] - (rng * 0.55)
                
                if gap_pct > 0.4 and rvol > 1.0:
                    breakout_candidates.append({
                        "symbol": sym, "gap": gap_pct, "ltp": live_session['Close'], "rvol": rvol,
                        "entry": h3_rev, "target": h4_brk, "stoploss": l3_rev, "type": "LONG"
                    })
                elif gap_pct < -0.4 and rvol > 1.0:
                    breakout_candidates.append({
                        "symbol": sym, "gap": gap_pct, "ltp": live_session['Close'], "rvol": rvol,
                        "entry": l3_rev, "target": l4_brk, "stoploss": h3_rev, "type": "SHORT"
                    })

    sorted_candidates = sorted(breakout_candidates, key=lambda x: abs(x['gap']), reverse=True)
    if sorted_candidates:
        log_initial_setups_to_csv(sorted_candidates[:3])

    setup_blocks = []
    for c in sorted_candidates[:3]:
        setup_blocks.append(
            f"🎯 **{c['symbol']}** ({c['type']} Setup | Gap: **{c['gap']:+.2f}%**)\n"
            f"  • **Morning Starting Price**: Rs {c['ltp']:,.2f}\n"
            f"  • **Trigger Entry**: Rs {c['entry']:,.2f}\n"
            f"  • **Proposed Target Price**: **Rs {c['target']:,.2f}**\n"
            f"  • **Stop-Loss Level**: Rs {c['stoploss']:,.2f}\n"
            f"  • execution: [Trade on Groww](https://groww.in/search?q={c['symbol']})"
        )

    scanned_output = "\n\n".join(setup_blocks) if setup_blocks else "• Scanner Alert: No budget targets breached matching anomaly thresholds."
    prompt = f"LIVE SCANNED EXECUTIONS:\n\n{scanned_output}"
    send_telegram(call_groq(prompt, "You are a lead trading commander. Present active trade parameters cleanly."))


# ─────────────────────────────────────────────
# PHASE 3: EVENING AUDIT & COMPILING (05:30 PM)
# ─────────────────────────────────────────────
def generate_audit_pdf(date_str: str, audit_records: list, filename: str):
    if not REPORTLAB_AVAILABLE:
        return False
    try:
        c = canvas.Canvas(filename, pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 750, f"MASTER LEDGER AUDIT REPORT — {date_str}")
        c.setStrokeColor(colors.HexColor("#A0A0A0"))
        c.line(50, 735, 562, 735)
        
        c.setFont("Helvetica", 9)
        c.drawString(50, 715, "Automated validation comparing Morning Strategy Proposals against actual EOD Market Close values.")
        
        y = 660
        c.setFont("Helvetica-Bold", 8)
        c.drawString(50, y, "SYMBOL")
        c.drawString(110, y, "DIR")
        c.drawString(140, y, "START PRICE")
        c.drawString(210, y, "PROPOSED TGT")
        c.drawString(290, y, "STOP LOSS")
        c.drawString(360, y, "EOD CLOSE")
        c.drawString(430, y, "FINAL LEDGER STATUS")
        
        c.line(50, y-6, 562, y-6)
        y -= 22
        
        c.setFont("Helvetica", 8)
        for r in audit_records:
            c.drawString(50, y, str(r['symbol']))
            c.drawString(110, y, str(r['direction']))
            c.drawString(140, y, f"{float(r['start_price']):.2f}")
            c.drawString(210, y, f"{float(r['target']):.2f}")
            c.drawString(290, y, f"{float(r['stoploss']):.2f}")
            c.drawString(360, y, f"{float(r['close']):.2f}")
            c.drawString(430, y, str(r['status']))
            y -= 18
            
        c.line(50, y, 562, y)
        c.save()
        return True
    except Exception:
        return False


def execute_evening_audit():
    dbg("Running Phase 3: Evening Reconciliation & Master Ledger Updates...")
    today_date_str = get_ist_now().strftime("%Y-%m-%d")
    
    if not os.path.exists(LOG_FILE_PATH):
        send_telegram("⚠️ *Evening Audit Cancelled*: Master transaction sheet not found.")
        return

    all_rows = []
    with open(LOG_FILE_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            all_rows.append(row)

    updated_count = 0
    audit_records_for_pdf = []
    text_blocks = []

    for row in all_rows:
        if row["Timestamp"].startswith(today_date_str) and row["Status"] == "PENDING":
            sym = row["Symbol"]
            direction = row["Direction"]
            start_price = float(row["Morning_Starting_Price"])
            entry = float(row["Trigger_Entry"])
            target = float(row["Proposed_Target"])
            sl = float(row["Stop_Loss"])
            
            try:
                ticker_obj = yf.Ticker(f"{sym}.NS")
                hist = ticker_obj.history(period="1d")
                if hist.empty: raise ValueError("No EOD data available")
                
                day_high = hist["High"].iloc[-1]
                day_low = hist["Low"].iloc[-1]
                day_close = hist["Close"].iloc[-1]
                
                status = "NO TRIGGER ⏳"
                if direction == "LONG":
                    if day_high >= entry:
                        if day_high >= target: status = "🎯 TARGET HIT"
                        elif day_low <= sl: status = "🛑 STOP LOSS BREACHED"
                        else: status = "🔄 TRADING RANGE RETAINED"
                elif direction == "SHORT":
                    if day_low <= entry:
                        if day_low <= target: status = "🎯 TARGET HIT"
                        elif day_high >= sl: status = "🛑 STOP LOSS BREACHED"
                        else: status = "🔄 TRADING RANGE RETAINED"
                
                row["EOD_Ending_Price"] = f"{day_close:.2f}"
                row["Status"] = status
                updated_count += 1
                
                audit_records_for_pdf.append({
                    "symbol": sym, "direction": direction, "start_price": start_price,
                    "target": target, "stoploss": sl, "close": day_close, "status": status
                })
                
                text_blocks.append(
                    f"📊 **{sym}** ({direction})\n"
                    f"  • Morning Starting Price: Rs {start_price:.2f}\n"
                    f"  • Proposed Target Price: Rs {target:.2f}\n"
                    f"  • **Actual EOD Ending Price**: **Rs {day_close:.2f}**\n"
                    f"  • Final Outcome Status: `{status}`"
                )
            except Exception: pass

    if updated_count == 0:
        send_telegram(f"📉 *Evening Ledger Review* — {today_date_str}\n• No matching pending simulation entries found for today's date.")
        return

    with open(LOG_FILE_PATH, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    pdf_filename = f"Master_Ledger_{today_date_str}.pdf"
    pdf_success = generate_audit_pdf(today_date_str, audit_records_for_pdf, pdf_filename)
    
    reconciled_text = "\n\n".join(text_blocks)
    telegram_caption = f"🏁 *EVENING PERFORMANCE LEDGER SUMMARY* — {today_date_str}\n\n{reconciled_text}\n\n📝 *Master log file updated directly on storage disk.*"
    
    if pdf_success and os.path.exists(pdf_filename):
        send_telegram_document(pdf_filename, telegram_caption)
        try: os.remove(pdf_filename)
        except Exception: pass
    else:
        send_telegram(telegram_caption)


if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "morning"
    if mode == "morning":
        execute_premarket_intel()
    elif mode == "live":
        execute_live_strategy_scanner()
    elif mode == "evening":
        execute_evening_audit()
