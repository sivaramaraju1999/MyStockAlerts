#!/usr/bin/env python3
"""
LIVE TRADING INDIAN BOT WITH REAL-TIME INTRADAY MONITORING
==========================================================

This bot provides:
1. Signal generation (morning, midday, evening, bond, ipo, weekend reports)
2. CONTINUOUS INTRADAY MONITORING for downside moves from day's open (LIVE DATA)
3. SEBI SEMI-AUTOMATED COMPLIANT (AI advises, human executes)

LIVE TRADING FEATURES:
- Connects to Indian broker APIs for real-time price data
- Supports Zerodha Kite, Dhan, Upstox APIs
- Flexible broker interface - easy to add new brokers
- Real-time downside alerts from day's open price
- Actionable guidance for manual trade management

IMPORTANT DISCLAIMER:
This is educational content only, not SEBI registered advice.
You must manually place all orders through your broker.
Start with small position sizes and never risk more than you can afford to lose.
Past performance does not guarantee future results.

Author: Enhanced for live trading with real broker integration
Version: 3.0 (Live Trading Ready)
Last Updated: 2026-06-23
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timedelta
import pytz
import threading
import abc
from abc import ABC, abstractmethod

# ─────────────────────────────────────────────
# CREDENTIALS — loaded from environment variables
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")

# Broker API credentials (set as environment variables)
ZERODHA_API_KEY    = os.environ.get("ZERODHA_API_KEY", "")
ZERODHA_ACCESS_TOKEN = os.environ.get("ZERODHA_ACCESS_TOKEN", "")
DHAN_CLIENT_ID     = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN  = os.environ.get("DHAN_ACCESS_TOKEN", "")
UPSTOX_API_KEY     = os.environ.get("UPSTOX_API_KEY", "")
UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
# Signal generation
SIGNAL_INTERVALS = {
    "morning": "08:00",   # 8:00 AM IST
    "midday": "12:00",    # 12:00 PM IST
    "evening": "15:45",   # 3:45 PM IST
}

# Continuous monitoring
MONITOR_INTERVAL = 30          # seconds between price checks
DOWNSIDE_ALERT_1 = 0.5         # % move down from day's open to trigger first alert
DOWNSIDE_ALERT_2 = 1.0         # % move down from day's open to trigger second alert
DOWNSIDE_ALERT_3 = 1.5         # % move down from day's open to trigger third alert
MAX_ALERTS_PER_STOCK = 3       # max alerts per stock to avoid spam
MARKET_OPEN = IST.localize(datetime.now().replace(hour=9, minute=15, second=0))
MARKET_CLOSE = IST.localize(datetime.now().replace(hour=15, minute=30, second=0))

# ─────────────────────────────────────────────
# RETRY CONFIGURATION
# ─────────────────────────────────────────────
GROQ_TIMEOUT         = 180          # seconds per attempt
GROQ_MAX_RETRIES     = 3
GROQ_RETRY_DELAY     = 15           # seconds between retries
TELEGRAM_TIMEOUT     = 30
TELEGRAM_MAX_RETRIES = 3
TELEGRAM_RETRY_DELAY = 5


def get_ist_now():
    return datetime.now(IST)


def is_market_open():
    now = get_ist_now()
    return MARKET_OPEN.time() <= now.time() <= MARKET_CLOSE.time() and now.weekday() < 5


def dbg(msg: str):
    """Timestamped debug print to stdout."""
    ts = get_ist_now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message: str, is_error: bool = False):
    """
    Send a Telegram message, splitting at 4000-char chunks.
    Retries up to TELEGRAM_MAX_RETRIES times on failure.
    If is_error=True the prefix is already set by caller.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4000
    chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]

    for idx, chunk in enumerate(chunks):
        dbg(f"Sending Telegram chunk {idx+1}/{len(chunks)} ({len(chunk)} chars)...")
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       chunk,
            "parse_mode": "Markdown",
        }
        for attempt in range(1, TELEGRAM_MAX_RETRIES + 1):
            try:
                r = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
                r.raise_for_status()
                dbg(f"Telegram chunk {idx+1} sent successfully (attempt {attempt}).")
                break
            except Exception as e:
                dbg(f"[WARN] Telegram send attempt {attempt}/{TELEGRAM_MAX_RETRIES} failed: {e}")
                if attempt < TELEGRAM_MAX_RETRIES:
                    dbg(f"Retrying Telegram in {TELEGRAM_RETRY_DELAY}s...")
                    time.sleep(TELEGRAM_RETRY_DELAY)
                else:
                    # Last resort — try plain text (strip markdown that may have caused the error)
                    dbg("[ERROR] All Telegram retries exhausted for this chunk. Trying plain text...")
                    try:
                        plain_payload = {
                            "chat_id":    TELEGRAM_CHAT_ID,
                            "text":       chunk,
                            "parse_mode": "",
                        }
                        r2 = requests.post(url, json=plain_payload, timeout=TELEGRAM_TIMEOUT)
                        r2.raise_for_status()
                        dbg("Sent chunk as plain text (fallback).")
                    except Exception as e2:
                        dbg(f"[CRITICAL] Plain-text fallback also failed: {e2}")


def send_error_to_telegram(context: str, error: str):
    """Send a structured error alert to Telegram so failures are visible."""
    now = get_ist_now().strftime("%d %b %Y %I:%M %p IST")
    msg = (
        f"🚨 *BOT ERROR — {now}*\n\n"
        f"*Context:* `{context}`\n"
        f"*Error:* `{error[:800]}`\n\n"
        f"_Check logs for full traceback._"
    )
    dbg(f"Sending error notification to Telegram: {error[:120]}")
    send_telegram(msg, is_error=True)


# ─────────────────────────────────────────────
# GROQ
# ─────────────────────────────────────────────
def call_groq(prompt: str, context: str = "groq_call") -> str:
    """
    Call Groq API with structured prompting using Llama 3.3 70B.
    Retries up to GROQ_MAX_RETRIES times with exponential back-off.
    On complete failure, reports to Telegram and returns an error string.
    """
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
                "content": "You are an elite Indian financial market data intelligence AI assistant with comprehensive access to live global market summaries, NSE/BSE analytics, IPO metrics, and bond market data. Your advice must be actionable, specific, and immediately executable by retail investors via manual order placement. You operate within SEBI's semi-automated trading framework - you generate trade ideas, but humans must manually execute orders. Provide exact price levels, clear risk management, and actionable steps."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.1,  # Lower temperature for more consistent, actionable advice
        "max_tokens": 4096,
    }

    last_error = ""
    for attempt in range(1, GROQ_MAX_RETRIES + 1):
        dbg(f"Groq API attempt {attempt}/{GROQ_MAX_RETRIES} (timeout={GROQ_TIMEOUT}s)...")
        try:
            r = requests.post(url, headers=headers, json=body, timeout=GROQ_TIMEOUT)
            dbg(f"Groq HTTP status: {r.status_code}")
            r.raise_for_status()
            data = r.json()

            # ── Extract usage token counts ──
            usage = data.get("usage", {})
            dbg(
                f"Tokens — prompt: {usage.get('prompt_tokens', '?')} | "
                f"completion: {usage.get('completion_tokens', '?')} | "
                f"total: {usage.get('total_tokens', '?')}"
            )

            # ── Extract text response from Groq/OpenAI format ──
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("Empty response choices from Groq.")

            result = choices[0].get("message", {}).get("content", "").strip()

            if not result:
                raise ValueError("Empty text response content from Groq.")

            dbg(f"Groq returned {len(result)} characters.")
            return result

        except requests.exceptions.Timeout:
            last_error = f"Request timed out after {GROQ_TIMEOUT}s"
            dbg(f"[WARN] {last_error}")
        except requests.exceptions.HTTPError as e:
            last_error = f"HTTP {r.status_code}: {r.text[:300]}"
            dbg(f"[WARN] Groq HTTP error: {last_error}")
        except Exception as e:
            last_error = str(e)
            dbg(f"[WARN] Groq error: {last_error}")

        if attempt < GROQ_MAX_RETRIES:
            wait = GROQ_RETRY_DELAY * attempt   # 15s, 30s, 45s ...
            dbg(f"Retrying Groq in {wait}s...")
            time.sleep(wait)

    # ── All retries exhausted ──
    err_msg = f"Groq failed after {GROQ_MAX_RETRIES} attempts. Last error: {last_error}"
    dbg(f"[ERROR] {err_msg}")
    send_error_to_telegram(context, err_msg)
    return f"[ERROR] {err_msg}"


# ─────────────────────────────────────────────
# PROMPTS - OPTIMIZED FOR ACTIONABLE INDIAN MARKET ADVICE
# ─────────────────────────────────────────────
def build_prompt(report_type: str) -> str:
    now       = get_ist_now()
    date_str  = now.strftime("%A, %d %B %Y")
    time_str  = now.strftime("%I:%M %p IST")
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    open_mins = max(0, int((market_open - now).total_seconds() // 60))

    if report_type == "morning":
        return f"""
        You are an elite Indian stock market analyst and trading advisor. Today is {date_str}, {time_str}.
        Indian market opens at 9:15 AM IST ({open_mins} minutes from now).

        YOUR MISSION:
        Provide SPECIFIC, ACTIONABLE trading advice for MANUAL execution that complies with SEBI's semi-automated trading framework.
        Focus on HIGH CONFIDENCE setups with clear entry, target, and stop loss levels suitable for retail investors.

        MANDATORY DATA EVALUATION (Indian Market Focus):
        1. Pre-market: "GIFT Nifty today {date_str}" - actual value, change, and volume
        2. Global: "Asian markets closing {date_str}" - Nifty futures, SGX Nifty, plus US Dow/S&P/NASDAQ
        3. Core: Crude oil (Brent), USD/INR, Gold, 10Y G-Sec yield
        4. Domestic:
           - "NSE BSE pre-market top gainers losers {date_str}" - exact names and %
           - "FII DII net data {date_str}" - exact inflow/outflow in crores
           - "India VIX today {date_str}" - current value and change
           - "Sector rotation NSE {date_str}" - which sectors seeing institutional flow
        5. IPO: Current Mainboard/SME issues, GMP, subscription

        INTRADAY TRADING SETUPS (provide EXACTLY 5 HIGH CONFIDENCE trades):
        For each trade, provide MANUALLY EXECUTABLE advice:
        - Stock name and symbol (NSE: XXXX or BSE: XXXX)
        - Exact entry price range (₹X - ₹Y) - based on current pre-market or expected open
        - Target 1 (₹Z) and Target 2 (₹W) - with % upside
        - Stop Loss (₹V) - with % downside (max 1.5-2% for intraday)
        - Risk-Reward ratio (minimum 1:2.5, preferably 1:3+)
        - Specific catalyst (news, results, event) with time and source
        - Time horizon (e.g., "Intraday - target exit by 2:30 PM" or "Next 2-3 hours")
        - Position sizing (max 1.5% of trading capital per trade)
        - Invalidation level (if price moves to ₹[Z], cancel trade)
        - Order type suggestion (Market/Limit/Stop-Limit)

        BOND/FIXED INCOME (provide EXACTLY 2 opportunities suitable for retail):
        For each, provide:
        - Instrument name and ISIN
        - Current yield/YTM (%)
        - Credit rating (with agency)
        - Maturity date
        - Minimum investment (₹ amount)
        - Current price/trading level
        - Buy range (specific levels
        - Why attractive now (specific Indian market reason)
        - Action: BUY/ACCUMULATE/AVOID

        OUTPUT FORMAT — use EXACTLY this Telegram markdown format:

        📈 *MANUAL TRADING SIGNALS — {date_str}*
        _{time_str} | Market opens in {open_mins} mins | SEBI Semi-Auto Compliant_

        ━━━━━━━━━━━━━━━━━━━━
        🇮🇳 *INDIAN MARKET STATUS*
        ━━━━━━━━━━━━━━━━━━━━
        • GIFT Nifty: [value] ([change]%) | Vol: [X] vs avg
        • SGX Nifty: [value] ([change]%)
        • Asian Close: Nikkei [X%], Hang Seng [Y%]
        • US Futures: Dow [X%], S&P [Y%], NASDAQ [Z%]
        • Crude Oil: [price] USD/bbl ([change]%)
        • USD/INR: [rate] ([change]%)
        • Gold: [price] USD/oz ([change]%)
        • 10Y G-Sec: [yield]% ([change] bps)
        • India VIX: [value] ([change]%)
        • FII/DII: FII [+X/-Y]₵, DII [+X/-Y]₵
        • Sentiment: 🟢 STRONGLY BULLISH / 🟢 BULLISH / 🟡 NEUTRAL / 🔴 BEARISH / 🔴 STRONGLY BEARISH

        ━━━━━━━━━━━━━━━━━━━━
        ⚡ *HIGH-CONVICTION SETUPS (MAX 3 CONCURRENT TRADES)*
        ━━━━━━━━━━━━━━━━━━━━

        🔴 *SETUP 1: [STOCK NAME] (NSE: [SYMBOL])*
        Action: 🟢 BUY or 🔴 SELL
        Entry Zone: ₹[XX.X] - ₹[YY.Y]  [Limit order suggestion: Buy at ₹[Z] or better]
        Target 1: ₹[ZZ.Z] (+[A]%) | Target 2: ₹[WW.W] (+[B]%)
        Stop Loss: ₹[VV.V] (-[C]%)  [Mandatory: Place SL order immediately]
        Risk-Reward: 1:[X]
        Catalyst: [Specific news - e.g., "Q3 results beat by 15% (NDTV Profit, 8:30 AM)"]
        Time Horizon: [e.g., "Intraday - exit by 1:00 PM" or "Next 90 minutes"]
        Position Size: [X]% of trading capital (Max ₹[Amount] per trade)
        Invalidation: [If price closes below/above ₹[Z] on 15-min chart, cancel]
        Order Type: [Limit at entry zone / Market if breaking [level]]
        Why Now: [1 sentence - specific Indian market reason]

        🔴 *SETUP 2: [STOCK NAME] (NSE: [SYMBOL])*
        [Same format as above]

        🔴 *SETUP 3: [STOCK NAME] (NSE: [SYMBOL])*
        [Same format as above]

        🔴 *SETUP 4: [STOCK NAME] (NSE: [SYMBOL])*
        [Same format as above]

        🔴 *SETUP 5: [STOCK NAME] (NSE: [SYMBOL])*
        [Same format as above]

        ━━━━━━━━━━━━━━━━━━━━
        📉 *FIXED INCOME OPPORTUNITIES*
        ━━━━━━━━━━━━━━━━━━━━

        🟢 *OPPORTUNITY 1: [BOND NAME] (ISIN: [INXXXXXXXXXX])*
        Type: [G-Sec/SDL/Taxable PSU Bank/Tax-free/Corporate]
        Rating: [AAA/AA+/AA/A - Agency e.g., "AAA (CRISIL)"]
        Yield/YTM: [X.XX]%
        Maturity: [Date]
        Face Value: ₹[Amount] (typically ₹100/₹1000)
        Current Price: ₹[Value] ([Y]% of face value)
        Buy Range: ₹[Low] - ₹[High]  [Limit orders suggested]
        Min. Investment: ₹[Amount] (lots of [Z] units)
        Action: BUY at [price] / ACCUMULATE on dips / AVOID
        Why Attractive: [Specific reason - e.g., "New SDL auction at attractive cut-off"]
        Horizon: [e.g., "Hold to maturity" or "Trade for 6-12 months"]

        🟢 *OPPORTUNITY 2: [BOND NAME] (ISIN: [INXXXXXXXXXX])*
        [Same format as above]

        ━━━━━━━━━━━━━━━━━━━━
        🎯 *IPO APPLICATION STRATEGY (RETAIL FOCUS)*
        ━━━━━━━━━━━━━━━━━━━━
        📋 OPEN IPOs: [List names with dates or "None open"]
        📊 LATEST SUBSCRIPTION: [If any, Retail: Xx, Xx, NII: Yx, QIB: Zx, Total: Tx]
        🎯 GETTING PICKED STRATEGY (SEBI-Compliant):
        • Apply at CUT-OFF price ONLY (maximizes allotment chance in lottery)
        • Use MULTIPLE PAN-linked demat accounts (family members)
        • Apply for EXACTLY 1 lot per account (not multiples - SEBI pro-rata lottery)
        • Submit application BEFORE 1:00 PM IST (earlier = better technical success)
        • Approve UPI mandate IMMEDIATELY after Application No. (within 5 mins)
        • Best time to apply: [Specific window - e.g., "10:30 AM - 12:30 PM IST"]
        • Avoid last hour (2:30-3:00 PM) - high technical rejection risk per NSE
        • Check GMP trend: [Rising/Falling/Stable] - apply only if GMP > 20%

        ━━━━━━━━━━━━━━━━━━━━
        💡 *TODAY'S EXECUTION DISCIPLINE*
        ━━━━━━━━━━━━━━━━━━━━
        [One specific actionable tip for today - e.g., "Wait for first 20 mins after open to avoid volatility spike"]

        _⚠️ For education only. Not SEBI registered advice. You must manually place orders. Start small._
        """

    elif report_type == "midday":
        return f"""
        You are an elite Indian stock market analyst providing REAL-TIME trading updates. Time is {time_str}, {date_str}.
        Indian market is LIVE (9:15 AM - 3:30 PM IST).

        YOUR MISSION:
        Provide IMMEDIATE, ACTIONABLE updates for trades I can MANUALLY execute RIGHT NOW based on current market action.

        EVALUATE THESE REAL-TIME INDIAN MARKET METRICS:
        1. Current Nifty 50 price, change, and volume profile
        2. Bank Nifty and sectoral indices performance
        3. Top 3 gainers and losers on NSE RIGHT NOW (with exact % and delivery % if available)
        4. Sector money flow (NSE sectoral data - where FII/DII moving)
        5. Any blockbuster deals, results, or news hitting wires in last 30 minutes (sources: Moneycontrol, Economic Times)
        6. Institutional activity on hourly charts (bulk/block deals)
        7. Options data - unusual activity in specific stocks (NSE OI change)

        Send an IMMEDIATE UPDATE in this format:

        ⚡ *REAL-TIME INDIAN MARKET UPDATE — {date_str}*
        _{time_str}_

        📈 *LIVE MARKET DATA (NSE/BSE)*
        • Nifty 50: [value] ([+/-X]%) | Volume: [X]% vs 20-day avg | Delivery: [Y]%
        • Bank Nifty: [value] ([+/-Y]%)
        • India VIX: [value] ([change]%)
        • Advance-Decline (NSE): [X] advances, [Y] declines
        • Sector Strength: [Top 3 sectors by advance/decline ratio]

        🟢 *IMMEDIATE BUY SIGNALS (ACT NOW - MANUAL)*
        [If any, provide: Stock (NSE: SYMBOL), Current LTP, Target, SL, Reason - max 2 signals]

        🔴 *IMMEDIATE SELL/EXIT SIGNALS*
        [If any, provide: Stock (NSE: SYMBOL), Current LTP, SL hit? (Y/N), Reason - max 2 signals]

        📊 *SECTOR ROTATION NOW (NSE DATA)*
        • Strongest Inflow: [Sector name] - [reason - e.g., "FII net buying in Banking per NSE bulk deal data"]
        • Strongest Outflow: [Sector name] - [reason]

        📰 *BREAKING NEWS (LAST 30 MIN - INDIA FOCUS)*
        • [Headline and source] → Impact: [Specific stocks/sectors affected]

        ⚡ *TRADE MANAGEMENT UPDATE*
        [Specific advice on morning trades - e.g., "TRADE 1 (RELIANCE): LTP ₹[X], move SL to breakeven at ₹[Y]"]

        🕒 *MARKET PHASE (INDIAN CONTEXT)*
        [e.g., "Opening range established", "Accumulation phase in FMCG", "Distribution in IT", "Choppy - wait for directional bias"]

        _Execute manually with strict SL discipline. Square off by 3:15 PM if not done._
        """

    elif report_type == "evening":
        return f"""
        You are an elite Indian stock market analyst providing END-OF-DAY analysis and NEXT DAY preparation. Time is {time_str}, {date_str}.
        Indian market has CLOSED for today (3:30 PM IST).

        YOUR MISSION:
        1. Review today's signals and performance
        2. Provide SPECIFIC watchlist and preparation for TOMORROW's trading
        3. Give overnight positioning advice for bonds and IPOs
        4. Note any positional adjustments needed for equity F&O (considering auto square-off)

        SYNTHESIZE TODAY'S INDIAN MARKET ACTION:
        1. Today's market close: Exact Nifty, Sensex values and % changes
        2. Session-wise performance: Opening, mid-day, closing action
        3. Sector performance: Best and worst performing sectors (with % and reason)
        4. Top 3 performers and losers of the day (with exact % and volume)
        5. FII/DII activity for the day (exact numbers from NSE)
        6. Bulk/block deal activity (if significant)
        7. Global cues for tomorrow (ASX 200, Nikkei, SGX Nifty, US futures)
        8. Overnight news that will impact opening (global + domestic)
        9. Macroeconomic data releases scheduled for tomorrow (Indian + global)
        10. Any relevant RBI/news impacting rates/liquidity

        Send evening summary in this format:

        🏁 *MARKET CLOSE & TOMORROW'S PREP — {date_str}*
        _{time_str}_

        📊 *TODAY'S PERFORMANCE (NSE/BSE)*
        • Nifty 50: [closing value] ([+/-X]%) | Volume: [X]% of 20-day avg
        • Sensex: [closing value] ([+/-Y]%)
        • Best Sector: [Sector] (+[A]%) - [reason]
        • Worst Sector: [Sector] (-[B]%) - [reason]
        • Advance-Decline: [X] advances, [Y] declines
        • Volume Shock: [X] stocks with >2x avg volume
        • FII: [+X/-Y] crores | DII: [+X/-Y] crores
        • India VIX: [value] ([change]% from open)

        🏆 *TOP PERFORMERS TODAY (NSE)*
        • [Stock]: +[X]% (Volume: [X] lacs) — [Specific reason with source]
        • [Stock]: +[Y]% — [Reason]
        • [Stock]: +[Z]% — [Reason]

        📉 *BOTTOM PERFORMERS TODAY (NSE)*
        • [Stock]: -[X]% (Volume: [X] lacs) — [Specific reason with source]
        • [Stock]: -[Y]% — [Reason]
        • [Stock]: -[Z]% — [Reason]

        📈 *TOMORROW'S PRE-MARKET PREP (9:15 AM IST)*
        • Intraday Watchlist: [Stock 1] ([NSE: SYMBOL] - [reason for watch]), [Stock 2], [Stock 3]
        • Key Levels: Nifty resistance at [level], support at [level] (based on Volume Profile)
        • Global Cues: SGX Nifty [value], Dow Futures [value], Nifty Futures OI change
        • Overnight News: [Specific news impacting tomorrow - e.g., "US CPI data tonight"]
        • Nifty Put-Call Ratio: [value] ([change])

        🎯 *TOMORROW'S IPO FOCUS*
        • IPOs Opening Tomorrow: [List or "None"]
        • IPOs Closing Tomorrow: [List or "None"]
        • Strategy: [Specific advice for tomorrow's IPOs - e.g., "Apply for [X] if GMP > 25%"]
        • Grey Market Watch: [IPO names] - GMP trend [Rising/Falling]

        📉 *BOND OVERNIGHT POSITIONING*
        • Recommendation: [Increase/Decrease/Maintain] bond exposure
        • Specific Instruments to Watch: [Names with ISINs and reasons]
        • Yield Targets: [Target yields for new purchases - e.g., "Look for SDLs > 7.2%"]
        • Action: [If increasing, specific buy levels]

        💡 *OVERNIGHT PREPARATION*
        [One specific action to take before market opens tomorrow - e.g., "Set price alerts for [stock] at [level]"]

        _Review notes. Prepare orders manually for tomorrow._
        """

    elif report_type == "bond_focus":
        return f"""
        You are a fixed income specialist providing SPECIFIC bond investment advice for the Indian retail investor. Today is {date_str}, {time_str}.

        YOUR MISSION:
        Provide EXECUTABLE bond investment recommendations with exact prices, yields, and actionable levels suitable for manual execution via platforms like RBI Retail Direct, NSE goBID, or broker platforms.
        Focus on opportunities that offer attractive risk-adjusted returns for Indian retail investors.

        ANALYZE INDIAN BOND MARKET:
        1. Current Indian bond market yields (G-Sec 10yr, 5yr, 3yr, T-bills)
        2. State Development Loans (SDLs) yields and recent auctions
        3. Corporate bond AAA/AA spreads over G-Sec (from NSE/BSE data)
        4. Upcoming bond auctions and issuances (goBID calendar)
        5. RBI policy impact and liquidity conditions (latest RBI bulletin)
        6. Tax-free bond opportunities (if any currently open)
        7. PSU/bank bond specifics
        8. Liquidity ETFs (LIQUIDBEES, LIQUIDCASE) as parking options

        Provide EXACTLY 3 BOND RECOMMENDATIONS:

        📊 *INDIAN BOND MARKET STATUS*
        • G-Sec 10Y Yield: [X.XX]% ([change vs yesterday] bps)
        • G-Sec 5Y Yield: [X.XX]% ([change] bps)
        • G-Sec 3Y Yield: [X.XX]% ([change] bps)
        • T-Bill 91D: [X.XX]% ([change] bps)
        • Corporate AAA Spread: [X] bps over 10Y G-Sec
        • Corporate AA Spread: [X] bps over 10Y G-Sec
        • SDL Average Yield: [X.XX]% (latest auction)
        • RBI Repo Rate: [X.XX]%
        • Inflation (CPI): [X.XX]% (latest)
        • Liquidity Condition: [Deficit/Surplus/Balanced] ₹[X] lacs crores (RBI data)
        • 10Y G-Sec RBI Cut-off (last auction): [value]%

        🟢 *RECOMMENDATION 1: [BOND NAME] (ISIN: [INXXXXXXXXXX])*
        Issuer: [Government of India/State Govt./PSU/Corporate/Bank]
        Type: [G-Sec/SDL/Taxable Bond/Tax-free Bond/PSU Bank Bond]
        Rating: [Sovereign/AAA/AA+/AA/A - Agency e.g., "Sovereign" or "AAA (CRISIL)"]
        Face Value: ₹[Amount] (typically ₹100 for G-Sec/SDL, ₹1000 for corporate)
        Coupon: [X.XX]%
        Frequency: [Annual/Semi-annual/Quarterly]
        Maturity: [Exact date - DD Mon YYYY]
        Current Price: ₹[Value] ([Y]% of face value)
        Yield to Maturity: [X.XX]%
        Yield to Call: [X.XX]% (if applicable, else NA)
        Min. Investment: ₹[Amount] (e.g., ₹10,000 for G-Sec via RBI Retail Direct)
        Action: BUY at ₹[Price] - ₹[Price]  [Limit order]
        Target Price: ₹[Value] ([Z]% upside)  or  Target Yield: [X.XX]%
        Stop Loss: ₹[Value] ([Z]% downside)  or  Stop Yield: [X.XX]%
        Horizon: [e.g., "Hold to maturity" or "Trade for 3-6 months"]
        Why Now: [Specific Indian catalyst - e.g., "RBI OMOs signaling softening stance"]
        Risk: [LOW/MEDIUM/HIGH] - [brief reason - e.g., "Low: Sovereign guarantee"]

        🟢 *RECOMMENDATION 2: [BOND NAME] (ISIN: [INXXXXXXXXXX])*
        [Same format as above]

        🟢 *RECOMMENDATION 3: [BOND NAME] (ISIN: [INXXXXXXXXXX])*
        [Same format as above]

        📈 *INDIAN BOND TRADING STRATEGIES*
        • Duration Play: [Advice on duration positioning based on yield curve]
        • Credit Spread Trade: [Specific spread trade idea - e.g., "Buy AAA PSU bonds, sell AA corporates"]
        • Yield Curve Strategy: [Advice based on curve shape - e.g., "Steepening - favor longer end"]
        • Tax Optimization: [If applicable - e.g., "Consider 54EC bonds for capital gains tax exemption"]
        • Liquidity Parking: [When equity overvalued, use LIQUIDBEES/LIQUIDCASE or T-bills]

        _Fixed income advice. Not SEBI registered. Use RBI Retail Direct or broker platforms for execution._
        """

    elif report_type == "ipo":
        return f"""
        You are an expert Indian IPO analyst specializing in RETAIL APPLICATION STRATEGIES for maximizing allotment chances within SEBI's regulatory framework. Today is {date_str}, {time_str}.

        YOUR MISSION:
        1. Identify IPOs/FPOs closing TODAY that retail investors should consider (mainboard & SME)
        2. Provide SPECIFIC, ACTIONABLE strategies to MAXIMIZE allotment probability (SEBI lottery system)
        3. Give exact timing and execution instructions for manual application
        4. Provide profit-taking strategies if allotted (manual execution)
        5. Remind that SEBI uses pro-rata lottery for RII - no gaming possible, only optimization

        ONLY send this message if at least 1 IPO/FPO closes TODAY with decent fundamentals.
        If no IPO closes today OR all closing IPOs are poor quality (GMP < 10%, weak fundamentals), reply with exactly: NO_IPO_CLOSING_TODAY

        ANALYZE EACH IPO (INDIAN MARKET FOCUS):
        1. Subscription data (latest) from NSE/BSE - retail, NII, QIB portions
        2. Grey Market Premium (GMP) trends and accuracy (sources: Chittorgarh, IPO Watch)
        3. Fundamentals: business model, financials (quarterly), valuation, promoter holding
        4. Issue size and price band (₹ amounts)
        5. Lot size and application process (ASBA/UPI via banks/brokers)
        6. Listing exchange and expected date
        7. Risk factors from DRHP

        If IPOs are closing today, use this format:

        ⏰ *LAST CHANCE — IPO CLOSING TODAY!*
        _{time_str} | {date_str}_

        🎯 *RETAIL ALLOTMENT MAXIMIZATION (SEBI LOTTERY SYSTEM)*
        These tactics IMPROVE your chances in the pro-rata lottery:
        • Apply at CUT-OFF price - NEVER at band lower/upper (mathematically optimal)
        • Use 3-4 demat accounts (different PAN cards - spouse, parents, adult children)
        • Apply for EXACTLY 1 lot per account (SEBI pro-rata: more lots ≠ higher probability)
        • Submit application BEFORE 1:00 PM IST (earlier submission = better technical success)
        • Approve UPI mandate IMMEDIATELY after Application No. is generated (within 5 mins)
        • Avoid applying between 2:30-3:00 PM IST (high technical rejection window per NSE)
        • If applying via ASBA, ensure sufficient balance in linked bank account (to avoid rejection)
        • Double-check DP ID and Client ID before submitting (common rejection reason)
        • Consider applying through broker vs direct bank (some have better success rates)

        For EACH IPO closing today with DECENT fundamentals (GMP > 15%, reasonable valuation):

        🔔 *[COMPANY NAME] IPO — CLOSES TODAY {date_str}*
           Issue Price: ₹[XXX] - ₹[YYY] | Lot: [Z shares] = ₹[Investment per lot]
           Current Subscription: Retail [R]x | HNI [H]x | QIB [Q]x | Total [T]x
           GMP Right Now: ₹[G] ([+P]% profit per lot if listed at GMP)
           GMP Trend: [Rising/Falling/Stable] over last 3 days (source: Chittorgarh)
           Listing Expectations: [NSE/BSE] on [Date]

           📊 FUNDAMENTALS SNAPSHOT (investment quality for retail):
           • Business: [1 line - what they actually do, simple]
           • Quarterly Results: [Profit: ₹X cr or Loss: ₹Y cr] - [QoQ/YoY change]
           • Debt:Equity: [ratio] - [Trend: Improving/Deteriorating/Stable]
           • Promoter Holding: [X]% - [Pledged: Y% or Nil]
           • Valuation: P/E [X] vs Industry Median [Y] - [Undervalued/Fair/Overvalued]
           • Revenue Growth (3Y CAGR): [X]% - [Source: Moneycontrol/ screener]
           • ROE: [X]% - [Last FY]
           • IPO Price vs Fair Value: [Discount/Premium] of [Z]% (based on avg peer multiples)

           ✅ SHOULD YOU APPLY FOR ALLOTMENT?
           Verdict: [STRONGLY APPLY / APPLY / WEAK APPLY / AVOID]
           Allotment Probability: [High/Medium/Low] - [Estimated % chance based on subscription]
           Reason: [2-3 specific reasons - e.g., "Strong Q3 results, retail subscription < 15x, promoter buying"]

           ⚡ EXECUTION TIMELINE (MANUAL PROCESS):
           • Best Time to Apply: [HH:MM] - [HH:MM] IST (e.g., "11:00 AM - 1:00 PM")
           • UPI Approval Deadline: [HH:MM] IST (do not delay - typically 5 PM same day)
           • Application No. Check: [Specific time after applying - e.g., "Check SMS/email in 10 mins"]
           • Status Check: [Where to check - e.g., "Broker portal or bank's ASBA portal"]

           💰 PROFIT STRATEGY IF ALLOTTED (MANUAL EXECUTION):
           • At GMP Listing: Sell [X]% immediately (profit booking), hold [Y]% for [Z] days
           • Target Price: ₹[Value] ([A]% gain from issue price)
           • Stop Loss: ₹[Value] ([B]% loss from listing price)
           • Hold Categorisation: [Flip for listing gain / Hold for 1 month / Hold for 6 months+ based on fundamentals]
           • Relay: [If applicable - e.g., "Consider selling 50% on listing, rest based on quarterly results"]

           📅 KEY DATES:
           • Allotment: [Date] (check after 5:00 PM via registrar/broker/bank)
           • Listing: [Date] on [Exchange] - trading starts at [time]
           • Registrar: [Name] — Check status at: [URL - e.g., "Link Intime: www.linkintime.co.in"]
           • Bank A/c Unblock: [Date] (typically allotment date + 1 day - check with bank)

        _Apply only after verifying fundamentals. Not SEBI registered advice. Manual application required._
        """

    elif report_type == "weekend":
        return f"""
        Today is {now.strftime('%A, %d %B %Y')}. Indian markets are closed on weekends.

        YOUR MISSION:
        Provide ACTIONABLE weekend preparation for next week's trading and investing.
        Focus on preparation steps I can take NOW to be ready for Monday's manual execution.

        FORMULATE:
        1. Strategic summary of past week's key market movements (Indian context)
        2. Concrete preparation for next week's opportunities
        3. Specific stock research for the weekend (using screener.in, moneycontrol)
        4. Weekend IPO application strategy (for any open issues)
        5. Preparation for upcoming week's economic events (RBI, global data)

        Send a weekend investor update in this format:

        🗓️ *WEEKEND TRADING PREP (INDIA FOCUS)*
        _{now.strftime('%A, %d %B %Y')}_

        📊 *PAST WEEK REVIEW (NSE/BSE)*
        • Nifty 50 Weekly Change: [X]%
        • Nifty 50 Weekly Volume: [X]% vs avg
        • Best Performing Sector: [Sector] (+[Y]%) - [reason]
        • Worst Performing Sector: [Sector] (-[Z]%) - [reason]
        • Key Event: [Most impactful event of the week - e.g., "RBI policy", "US non-farm payrolls"]
        • FII/DII Weekly: FII [+X/-Y] crores, DII [+X/-Y] crores
        • India VIX Weekly Change: [X]%

        🔧 *WEEKEND ACTION ITEMS (DO THESE NOW FOR MONDAY)*
        1. [Specific action - e.g., "Demarket: Review pending orders, adjust SLs for positional trades"]
        2. [Specific action - e.g., "Research: Screen for stocks with >20% QoQ profit growth using screener.in"]
        3. [Specific action - e.g., "Demat: Ensure sufficient margin in trading account for Monday"]
        4. [Specific action - e.g., "Alerts: Set price alerts for [stock] at [level] and [stock] at [level]"]
        5. [Specific action - e.g., "IPO: Check demat readiness for any open issues"]

        📈 *NEXT WEEK'S TRADING OPPORTUNITIES (INDIAN MARKET)*
        • Expected Volatility: [High/Medium/Low] - [reason - e.g., "High due to RBI policy + global cues"]
        • Key Levels to Watch:
            - Nifty: Support [X] (based on Volume Profile), Resistance [Y]
            - Bank Nifty: Support [X], Resistance [Y]
        • Sector in Focus: [Sector] - [specific reason - e.g., "Anticipating strong Q3 results in Banking"]
        • Stocks to Watch Long: [Stock 1] ([NSE: SYMBOL] - [reason - e.g., "Breaking 200 DMA on volume"]), [Stock 2], [Stock 3]
        • Stocks to Watch Short: [Stock 1] ([NSE: SYMBOL] - [reason - e.g., "Facing resistance + deteriorating fundamentals"]), [Stock 2]

        🎯 *WEEKEND IPO STRATEGY*
        • Open IPOs Right Now: [List or "None"]
        • Recommendation: [Apply/Skip/Wait for specific reason - e.g., "Apply for [X] if GMP > 25%"]
        • IPOs Opening Next Week: [List with dates]
        • Research These: [Names] - [what to check - e.g., "Check DRHP for [X], visit screener.in for financials"]

        📅 *UPCOMING WEEK KEY EVENTS (INDIAN + GLOBAL)*
        • RBI-related: [Any policy meet, data release] - [Expected market impact]
        • Global: [Fed data, geopolitical event, etc.] - [Expected market impact]
        • Domestic: [GST data, manufacturing PMI, agri data] - [Expected market impact]
        • Earnings: [Key results dates - e.g., "TCS Infosys results on Tuesday"]
        • Macro: [Union Budget preview, monsoon data, etc.]

        💡 *WEEKEND LEARNING EXERCISE*
        [One specific skill to practice - e.g., "Learn to read NSE bulk deal data for institutional clues"]

        _Markets reopen Monday 9:15 AM IST. Prepare your watchlist and orders._
        """

    return ""

def build_ipo_closing_prompt() -> str:
    now      = get_ist_now()
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M %p IST")
    return f"""
    You are an expert Indian IPO analyst specializing in RETAIL APPLICATION STRATEGIES for maximizing allotment chances within SEBI's regulatory framework. Today is {date_str}, {time_str}.

    YOUR MISSION:
    1. Identify IPOs/FPOs closing TODAY that retail investors should consider (mainboard & SME)
    2. Provide SPECIFIC, ACTIONABLE strategies to MAXIMIZE allotment probability (SEBI lottery system)
    3. Give exact timing and execution instructions for manual application
    4. Provide profit-taking strategies if allotted (manual execution)
    5. Remind that SEBI uses pro-rata lottery for RII - no gaming possible, only optimization

    ONLY send this message if at least 1 IPO/FPO closes TODAY with decent fundamentals.
    If no IPO closes today OR all closing IPOs are poor quality (GMP < 10%, weak fundamentals), reply with exactly: NO_IPO_CLOSING_TODAY

    ANALYZE EACH IPO (INDIAN MARKET FOCUS):
    1. Subscription data (latest) from NSE/BSE - retail, NII, QIB portions
    2. Grey Market Premium (GMP) trends and accuracy (sources: Chittorgarh, IPO Watch)
    3. Fundamentals: business model, financials (quarterly), valuation, promoter holding
    4. Issue size and price band (₹ amounts)
    5. Lot size and application process (ASBA/UPI via banks/brokers)
    6. Listing exchange and expected date
    7. Risk factors from DRHP

    If IPOs are closing today, use this format:

    ⏰ *LAST CHANCE — IPO CLOSING TODAY!*
    _{time_str} | {date_str}_

    🎯 *RETAIL ALLOTMENT MAXIMIZATION (SEBI LOTTERY SYSTEM)*
    These tactics IMPROVE your chances in the pro-rata lottery:
    • Apply at CUT-OFF price - NEVER at band lower/upper (mathematically optimal)
    • Use 3-4 demat accounts (different PAN cards - spouse, parents, adult children)
    • Apply for EXACTLY 1 lot per account (SEBI pro-rata: more lots ≠ higher probability)
    • Submit application BEFORE 1:00 PM IST (earlier submission = better technical success)
    • Approve UPI mandate IMMEDIATELY after Application No. is generated (within 5 mins)
    • Avoid applying between 2:30-3:00 PM IST (high technical rejection window per NSE)
    • If applying via ASBA, ensure sufficient balance in linked bank account (to avoid rejection)
    • Double-check DP ID and Client ID before submitting (common rejection reason)
    • Consider applying through broker vs direct bank (some have better success rates)

    For EACH IPO closing today with DECENT fundamentals (GMP > 15%, reasonable valuation):

    🔔 *[COMPANY NAME] IPO — CLOSES TODAY {date_str}*
       Issue Price: ₹[XXX] - ₹[YYY] | Lot: [Z shares] = ₹[Investment per lot]
       Current Subscription: Retail [R]x | HNI [H]x | QIB [Q]x | Total [T]x
       GMP Right Now: ₹[G] ([+P]% profit per lot if listed at GMP)
       GMP Trend: [Rising/Falling/Stable] over last 3 days (source: Chittorgarh)
       Listing Expectations: [NSE/BSE] on [Date]

       📊 FUNDAMENTALS SNAPSHOT (investment quality for retail):
       • Business: [1 line - what they actually do, simple]
           • Quarterly Results: [Profit: ₹X cr or Loss: ₹Y cr] - [QoQ/YoY change]
           • Debt:Equity: [ratio] - [Trend: Improving/Deteriorating/Stable]
           • Promoter Holding: [X]% - [Pledged: Y% or Nil]
           • Valuation: P/E [X] vs Industry Median [Y] - [Undervalued/Fair/Overvalued]
           • Revenue Growth (3Y CAGR): [X]% - [Source: Moneycontrol/ screener]
           • ROE: [X]% - [Last FY]
           • IPO Price vs Fair Value: [Discount/Premium] of [Z]% (based on avg peer multiples)

           ✅ SHOULD YOU APPLY FOR ALLOTMENT?
           Verdict: [STRONGLY APPLY / APPLY / WEAK APPLY / AVOID]
           Allotment Probability: [High/Medium/Low] - [Estimated % chance based on subscription]
           Reason: [2-3 specific reasons - e.g., "Strong Q3 results, retail subscription < 15x, promoter buying"]

           ⚡ EXECUTION TIMELINE (MANUAL PROCESS):
           • Best Time to Apply: [HH:MM] - [HH:MM] IST (e.g., "11:00 AM - 1:00 PM")
           • UPI Approval Deadline: [HH:MM] IST (do not delay - typically 5 PM same day)
           • Application No. Check: [Specific time after applying - e.g., "Check SMS/email in 10 mins"]
           • Status Check: [Where to check - e.g., "Broker portal or bank's ASBA portal"]

           💰 PROFIT STRATEGY IF ALLOTTED (MANUAL EXECUTION):
           • At GMP Listing: Sell [X]% immediately (profit booking), hold [Y]% for [Z] days
           • Target Price: ₹[Value] ([A]% gain from issue price)
           • Stop Loss: ₹[Value] ([B]% loss from listing price)
           • Hold Categorisation: [Flip for listing gain / Hold for 1 month / Hold for 6 months+ based on fundamentals]
           • Relay: [If applicable - e.g., "Consider selling 50% on listing, rest based on quarterly results"]

           📅 KEY DATES:
           • Allotment: [Date] (check after 5:00 PM via registrar/broker/bank)
           • Listing: [Date] on [Exchange] - trading starts at [time]
           • Registrar: [Name] — Check status at: [URL - e.g., "Link Intime: www.linkintime.co.in"]
           • Bank A/c Unblock: [Date] (typically allotment date + 1 day - check with bank)

        _Apply only after verifying fundamentals. Not SEBI registered advice. Manual application required._
        """


# ─────────────────────────────────────────────
# BROKER INTERFACE FOR LIVE PRICE DATA
# ─────────────────────────────────────────────
class BrokerPriceFeed(ABC):
    """Abstract base broker interface for fetching live prices"""

    @abstractmethod
    def get_ltp(self, symbol: str) -> float:
        """
        Get Last Traded Price for a symbol
        Returns: float price or 0.0 if error/not found
        """
        pass

    @abstractmethod
    def get_ohlc(self, symbol: str) -> dict:
        """
        Get Open, High, Low, Close for today
        Returns: dict with keys 'open', 'high', 'low', 'close' or empty dict
        """
        pass

    @abstractmethod
    def is_market_open(self) -> bool:
        """
        Check if market is currently open
        Returns: boolean
        """
        pass


class ZerodhaKiteFeed(BrokerPriceFeed):
    """Zerodha Kite Connect API implementation"""

    def __init__(self, api_key: str, access_token: str):
        self.api_key = api_key
        self.access_token = access_token
        self.base_url = "https://api.kite.trade"
        self.headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {api_key}:{access_token}"
        }

    def get_ltp(self, symbol: str) -> float:
        """Get LTP from Zerodha Kite"""
        try:
            # Format symbol for NSE (e.g., "RELIANCE" -> "NSE:RELIANCE")
            formatted_symbol = f"NSE:{symbol.upper()}" if ":" not in symbol else symbol

            url = f"{self.base_url}/quote/ltp"
            params = {"i": formatted_symbol}

            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                return float(data["data"][formatted_symbol]["last_price"])
            else:
                dbg(f"Zerodha API error: {data}")
                return 0.0
        except Exception as e:
            dbg(f"Error fetching LTP from Zerodha for {symbol}: {e}")
            return 0.0

    def get_ohlc(self, symbol: str) -> dict:
        """Get OHLC from Zerodha Kite"""
        try:
            formatted_symbol = f"NSE:{symbol.upper()}" if ":" not in symbol else symbol

            url = f"{self.base_url}/quote/ohlc"
            params = {"i": formatted_symbol}

            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                ohlc_data = data["data"][formatted_symbol]["ohlc"]
                return {
                    "open": float(ohlc_data["open"]),
                    "high": float(ohlc_data["high"]),
                    "low": float(ohlc_data["low"]),
                    "close": float(ohlc_data["close"])
                }
            else:
                dbg(f"Zerodha API error: {data}")
                return {}
        except Exception as e:
            dbg(f"Error fetching OHLC from Zerodha for {symbol}: {e}")
            return {}

    def is_market_open(self) -> bool:
        """Check market hours using Zerodha"""
        try:
            url = f"{self.base_url}/market"
            response = requests.get(url, headers=self.headers, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                return data["data"]["market_status"] == "open"
            else:
                return is_market_open()  # Fallback to time-based check
        except Exception as e:
            dbg(f"Error checking market status with Zerodha: {e}")
            return is_market_open()  # Fallback


class DhanFeed(BrokerPriceFeed):
    """Dhan API implementation"""

    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self.base_url = "https://api.dhan.co"
        self.headers = {
            "access-token": access_token,
            "client-id": client_id,
            "Content-Type": "application/json"
        }

    def get_ltp(self, symbol: str) -> float:
        """Get LTP from Dhan API"""
        try:
            # Dhan expects symbol in format like "EQ|INE002A01018" for ISIN or "NSE_EQ|RELIANCE" for trading symbol
            # For simplicity, we'll assume NSE format
            formatted_symbol = f"NSE_EQ|{symbol.upper()}"

            url = f"{self.base_url}/marketfeed/ltp"
            payload = {
                "symbols": [formatted_symbol]
            }

            response = requests.post(url, headers=self.headers, json=payload, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                return float(data["data"][formatted_symbol]["last_price"])
            else:
                dbg(f"Dhan API error: {data}")
                return 0.0
        except Exception as e:
            dbg(f"Error fetching LTP from Dhan for {symbol}: {e}")
            return 0.0

    def get_ohlc(self, symbol: str) -> dict:
        """Get OHLC from Dhan API"""
        try:
            formatted_symbol = f"NSE_EQ|{symbol.upper()}"

            url = f"{self.base_url}/marketdata/ohlc"
            payload = {
                "symbol": formatted_symbol,
                "expiry_code": 0  # For equity
            }

            response = requests.post(url, headers=self.headers, json=payload, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                ohlc_data = data["data"]
                return {
                    "open": float(ohlc_data["ohlc"]["open"]),
                    "high": float(ohlc_data["ohlc"]["high"]),
                    "low": float(ohlc_data["ohlc"]["low"]),
                    "close": float(ohlc_data["ohlc"]["close"])
                }
            else:
                dbg(f"Dhan API error: {data}")
                return {}
        except Exception as e:
            dbg(f"Error fetching OHLC from Dhan for {symbol}: {e}")
            return {}

    def is_market_open(self) -> bool:
        """Check market hours - Dhan doesn't have direct market status, use time-based"""
        return is_market_open()


class UpstoxFeed(BrokerPriceFeed):
    """Upstox API implementation"""

    def __init__(self, api_key: str, access_token: str):
        self.api_key = api_key
        self.access_token = access_token
        self.base_url = "https://api.upstox.com/v2"
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}"
        }

    def get_ltp(self, symbol: str) -> float:
        """Get LTP from Upstox API"""
        try:
            # Upstox uses instrument key format
            # We'll need to map symbol to instrument key - for simplicity, using a basic approach
            # In practice, you'd maintain a symbol-to-instrument-key mapping
            instrument_key = f"NSE_EQ|{symbol.upper()}"

            url = f"{self.base_url}/market-quote/ltp"
            params = {"instrument_key": instrument_key}

            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                return float(data["data"][instrument_key]["last_price"])
            else:
                dbg(f"Upstox API error: {data}")
                return 0.0
        except Exception as e:
            dbg(f"Error fetching LTP from Upstox for {symbol}: {e}")
            return 0.0

    def get_ohlc(self, symbol: str) -> dict:
        """Get OHLC from Upstox API"""
        try:
            instrument_key = f"NSE_EQ|{symbol.upper()}"

            url = f"{self.base_url}/market-quote/ohlc"
            params = {"instrument_key": instrument_key}

            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()
            if data["status"] == "success":
                ohlc_data = data["data"][instrument_key]["ohlc"]
                return {
                    "open": float(ohlc_data["open"]),
                    "high": float(ohlc_data["high"]),
                    "low": float(ohlc_data["low"]),
                    "close": float(ohlc_data["close"])
                }
            else:
                dbg(f"Upstox API error: {data}")
                return {}
        except Exception as e:
            dbg(f"Error fetching OHLC from Upstox for {symbol}: {e}")
            return {}

    def is_market_open(self) -> bool:
        """Check market hours"""
        return is_market_open()


# Broker factory
def create_broker_feed(broker_name: str = None) -> BrokerPriceFeed:
    """
    Create a broker feed instance based on available credentials
    Returns: BrokerPriceFeed instance or None if no credentials available
    """
    broker_name = broker_name or os.environ.get("DEFAULT_BROKER", "zerodha").lower()

    try:
        if broker_name == "zerodha" and ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN:
            return ZerodhaKiteFeed(ZERODHA_API_KEY, ZERODHA_ACCESS_TOKEN)
        elif broker_name == "dhan" and DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN:
            return DhanFeed(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
        elif broker_name == "upstox" and UPSTOX_API_KEY and UPSTOX_ACCESS_TOKEN:
            return UpstoxFeed(UPSTOX_API_KEY, UPSTOX_ACCESS_TOKEN)
        else:
            # Try to auto-detect based on available credentials
            if ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN:
                dbg("Auto-detected Zerodha credentials")
                return ZerodhaKiteFeed(ZERODHA_API_KEY, ZERODHA_ACCESS_TOKEN)
            elif DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN:
                dbg("Auto-detected Dhan credentials")
                return DhanFeed(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
            elif UPSTOX_API_KEY and UPSTOX_ACCESS_TOKEN:
                dbg("Auto-detected Upstox credentials")
                return UpstoxFeed(UPSTOX_API_KEY, UPSTOX_ACCESS_TOKEN)
            else:
                dbg("No broker credentials found - will use simulation mode")
                return None
    except Exception as e:
        dbg(f"Error creating broker feed: {e}")
        return None


# ─────────────────────────────────────────────
# CONTINUOUS INTRADAY MONITORING SYSTEM (LIVE READY)
# ─────────────────────────────────────────────
class IntradayMonitor:
    def __init__(self, broker_feed: BrokerPriceFeed = None):
        self.broker = broker_feed
        self.day_open_prices = {}      # symbol -> open price
        self.alerts_sent = {}          # symbol -> alert_level_count
        self.last_signal_context = {}  # symbol -> signal data for context
        self.is_running = False
        self.symbols_to_monitor = set()  # Symbols we're actively monitoring

    def set_broker(self, broker_feed: BrokerPriceFeed):
        """Set or update the broker feed"""
        self.broker = broker_feed

    def add_symbols_to_monitor(self, symbols: list):
        """Add symbols to monitor list"""
        self.symbols_to_monitor.update(symbols)
        dbg(f"Added {len(symbols)} symbols to monitor. Total: {len(self.symbols_to_monitor)}")

    def set_day_open(self, symbol: str, open_price: float):
        """Set the day's open price for a symbol"""
        self.day_open_prices[symbol] = open_price
        if symbol not in self.alerts_sent:
            self.alerts_sent[symbol] = 0
        dbg(f"Set day open for {symbol}: ₹{open_price:.2f}")

    def update_signal_context(self, symbol: str, signal_data: dict):
        """Update context from signal generation for better alerts"""
        self.last_signal_context[symbol] = signal_data

    def get_live_price(self, symbol: str) -> float:
        """Get live price from broker or return 0 if not available"""
        if not self.broker:
            dbg(f"No broker available for {symbol} - using simulation")
            return 0.0

        try:
            price = self.broker.get_ltp(symbol)
            if price <= 0:
                dbg(f"Invalid price received from broker for {symbol}: {price}")
            return price
        except Exception as e:
            dbg(f"Error fetching live price for {symbol}: {e}")
            return 0.0

    def get_day_open_price(self, symbol: str) -> float:
        """Get day's open price - from broker if available, else from our records"""
        # Try to get from broker first (more accurate)
        if self.broker:
            try:
                ohlc = self.broker.get_ohlc(symbol)
                if ohlc and "open" in ohlc:
                    return float(ohlc["open"])
            except Exception as e:
                dbg(f"Error fetching OHLC for {symbol} from broker: {e}")

        # Fallback to our recorded open price
        return self.day_open_prices.get(symbol, 0.0)

    def check_price_move(self, symbol: str, current_price: float) -> float:
        """
        Check if price has moved down from day's open
        Returns: percentage move down (positive if down, negative if up)
        """
        open_price = self.get_day_open_price(symbol)
        if open_price == 0:
            return 0.0

        # Calculate percentage move down from open
        move_down_pct = ((open_price - current_price) / open_price) * 100
        return move_down_pct

    def should_alert(self, symbol: str, move_down_pct: float) -> int:
        """
        Determine if we should send an alert and what level
        Returns: alert level (0=no alert, 1=first, 2=second, 3=third)
        """
        if symbol not in self.alerts_sent:
            self.alerts_sent[symbol] = 0

        current_level = self.alerts_sent[symbol]

        # Determine new alert level based on move down
        new_level = 0
        if move_down_pct >= DOWNSIDE_ALERT_3:
            new_level = 3
        elif move_down_pct >= DOWNSIDE_ALERT_2:
            new_level = 2
        elif move_down_pct >= DOWNSIDE_ALERT_1:
            new_level = 1

        # Only alert if we've reached a new level and haven't maxed out
        if new_level > current_level and current_level < MAX_ALERTS_PER_STOCK:
            return new_level
        return 0

    def send_downside_alert(self, symbol: str, move_down_pct: float, alert_level: int):
        """Send a Telegram alert for downside move from day's open"""
        open_price = self.get_day_open_price(symbol)
        if open_price == 0:
            dbg(f"Cannot get open price for {symbol} - skipping alert")
            return

        current_price = open_price - ((move_down_pct / 100) * open_price)
        now = get_ist_now().strftime("%I:%M %p IST")

        # Get signal context if available
        context_msg = ""
        if symbol in self.last_signal_context:
            signal = self.last_signal_context[symbol]
            context_msg = (
                f"\n📊 *Signal Context:*\n"
                f"• Catalyst: {signal.get('catalyst', 'N/A')}\n"
                f"• Time Horizon: {signal.get('time_horizon', 'N/A')}\n"
                f"• Original Entry: {signal.get('entry_zone', 'N/A')}\n"
            )

        # Determine alert severity
        if alert_level == 1:
            emoji = "🟡"
            severity = "WARNING"
            action = "Consider reviewing position - wait for confirmation before adding"
        elif alert_level == 2:
            emoji = "🟠"
            severity = "ALERT"
            action = "Consider reducing position size or tightening stops"
        else:  # alert_level == 3
            emoji = "🔴"
            severity = "CRITICAL"
            action = "Strongly consider exiting - significant adverse move from open"

        message = (
            f"{emoji} *INTRADAY DOWNSIDE ALERT — {severity}*\n"
            f"_{now}_\n\n"
            f"📉 *{symbol}*\n"
            f"• Day's Open: ₹{open_price:.2f}\n"
            f"• Current: ₹{current_price:.2f}\n"
            f"• Move Down: {move_down_pct:.2f}%\n"
            f"{context_msg}\n"
            f"💡 *Suggested Action:* {action}\n\n"
            f"_Not SEBI registered advice. Manual execution required._"
        )

        send_telegram(message)
        self.alerts_sent[symbol] = alert_level
        dbg(f"Sent downside alert for {symbol}: {move_down_pct:.2f}% down from open")

    def reset_daily(self):
        """Reset tracking for new trading day"""
        self.day_open_prices.clear()
        self.alerts_sent.clear()
        self.last_signal_context.clear()
        self.symbols_to_monitor.clear()
        dbg("Reset intraday monitoring for new day")


# Global monitor instance (will be initialized with broker if available)
broker_feed = create_broker_feed()
monitor = IntradayMonitor(broker_feed)


# ─────────────────────────────────────────────
# SIGNAL GENERATION FUNCTIONS
# ─────────────────────────────────────────────
def run_morning_report():
    now = get_ist_now()
    if now.weekday() >= 5:
        dbg("Weekend — skipping morning report.")
        return
    dbg("=== STARTING MORNING REPORT ===")
    send_telegram("🔄 _Scanning Indian markets, analyzing setups, preparing your manual trading signals..._")
    result = call_groq(build_prompt("morning"), context="morning_report")
    if result.startswith("[ERROR]"):
        dbg("Morning report failed — error already sent to Telegram.")
        return
    dbg(f"Morning report ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)

    # Extract stock symbols from signal for monitoring context
    # In a real implementation, this would parse the result to get symbols
    # For now, we'll note that signal context would be updated externally
    # In practice, you might save signals to a file and have the monitor read them
    # For this example, we'll use a predefined list - in reality, this comes from signals
    example_symbols_from_signals = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
                                   "SBIN", "BHARTIARTL", "ITC", "LT", "ASIANPAINT"]
    monitor.add_symbols_to_monitor(example_symbols_from_signals)

    # Try to get and set day's open prices for these symbols
    if broker_feed:
        dbg("Fetching day's open prices from broker...")
        for symbol in example_symbols_from_signals:
            open_price = monitor.get_day_open_price(symbol)
            if open_price > 0:
                monitor.set_day_open(symbol, open_price)
    else:
        dbg("No broker available - will use simulated prices for demonstration")
        # Set simulated open prices for demonstration
        import random
        for symbol in example_symbols_from_signals:
            if symbol not in monitor.day_open_prices:
                base_price = random.uniform(500, 3000)
                monitor.day_open_prices[symbol] = base_price

    dbg("=== MORNING REPORT DONE ===")

def run_bond_focus_report():
    now = get_ist_now()
    dbg("=== STARTING BOND FOCUS REPORT ===")
    send_telegram("🔄 _Analyzing Indian bond market opportunities..._")
    result = call_groq(build_prompt("bond_focus"), context="bond_report")
    if result.startswith("[ERROR]"):
        dbg("Bond report failed — error already sent to Telegram.")
        return
    dbg(f"Bond report ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== BOND REPORT DONE ===")

def run_ipo_closing_reminder():
    now = get_ist_now()
    if now.weekday() >= 5:
        dbg("Weekend — skipping IPO closing reminder.")
        return
    dbg("=== CHECKING IPO CLOSING REMINDER ===")
    result = call_groq(build_ipo_closing_prompt(), context="ipo_closing")
    if result.startswith("[ERROR]"):
        dbg("IPO closing check failed — error already sent to Telegram.")
        return
    if result.strip() == "NO_IPO_CLOSING_TODAY":
        dbg("No IPO closing today — skipping Telegram message.")
        return
    dbg(f"IPO reminder ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== IPO CLOSING REMINDER DONE ===")

def run_midday_report():
    now = get_ist_now()
    if now.weekday() >= 5:
        dbg("Weekend — skipping midday report.")
        return
    dbg("=== STARTING MIDDAY REPORT ===")
    result = call_groq(build_prompt("midday"), context="midday_report")
    if result.startswith("[ERROR]"):
        dbg("Midday report failed — error already sent to Telegram.")
        return
    dbg(f"Midday report ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== MIDDAY REPORT DONE ===")

def run_evening_report():
    now = get_ist_now()
    if now.weekday() >= 5:
        dbg("Weekend — skipping evening report.")
        return
    dbg("=== STARTING EVENING REPORT ===")
    result = call_groq(build_prompt("evening"), context="evening_report")
    if result.startswith("[ERROR]"):
        dbg("Evening report failed — error already sent to Telegram.")
        return
    dbg(f"Evening report ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== EVENING REPORT DONE ===")

def run_weekend_tip():
    now = get_ist_now()
    if now.weekday() not in [5, 6]:
        dbg("Weekday — skipping weekend tip.")
        return
    dbg("=== STARTING WEEKEND TIP ===")
    result = call_groq(build_prompt("weekend"), context="weekend_tip")
    if result.startswith("[ERROR]"):
        dbg("Weekend tip failed — error already sent to Telegram.")
        return
    dbg(f"Weekend tip ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== WEEKEND TIP DONE ===")


# ─────────────────────────────────────────────
# CONTINUOUS MONITORING MODE (LIVE TRADING READY)
# ─────────────────────────────────────────────
def run_intraday_monitor():
    """Run continuous intraday monitoring for downside moves from day's open (LIVE DATA)"""
    dbg("=== STARTING LIVE INTRADAY DOWNSIDE MONITOR ===")

    if broker_feed:
        dbg(f"Live trading mode: Connected to {type(broker_feed).__name__}")
    else:
        dbg("⚠️  WARNING: No broker connected - using SIMULATION MODE")
        dbg("   For live trading, set broker credentials in environment variables:")
        dbg("   ZERODHA_API_KEY, ZERODHA_ACCESS_TOKEN")
        dbg("   DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN")
        dbg("   UPSTOX_API_KEY, UPSTOX_ACCESS_TOKEN")

    # Reset for new day
    monitor.reset_daily()

    # Send startup message
    start_msg = (
        f"📡 *LIVE INTRADAY DOWNside MONITOR STARTED*\n"
        f"_{get_ist_now().strftime('%A, %d %B %Y %I:%M %p IST')}_\n\n"
        f"Monitoring for downside moves from day's open\n"
        f"Alert thresholds: {DOWNSIDE_ALERT_1}%, {DOWNSIDE_ALERT_2}%, {DOWNSIDE_ALERT_3}%\n"
        f"Max {MAX_ALERTS_PER_STOCK} alerts per stock\n"
        f"{'🔴 LIVE DATA MODE' if broker_feed else '🟡 SIMULATION MODE'}\n\n"
        f"_SEBI Semi-Auto Compliant - Manual execution required_"
    )
    send_telegram(start_msg)

    try:
        while is_market_open():
            # Get current list of symbols to monitor
            symbols_to_check = list(monitor.symbols_to_monitor)
            if not symbols_to_check:
                dbg("No symbols to monitor - waiting for signals...")
                time.sleep(MONITOR_INTERVAL)
                continue

            # Process each symbol
            for symbol in symbols_to_check:
                try:
                    # Get live price from broker
                    current_price = monitor.get_live_price(symbol)

                    if current_price <= 0:
                        # If we can't get live price, skip this symbol for this cycle
                        continue

                    # Check for downside move from day's open
                    move_down_pct = monitor.check_price_move(symbol, current_price)

                    # Determine if we should alert
                    alert_level = monitor.should_alert(symbol, move_down_pct)
                    if alert_level > 0:
                        monitor.send_downside_alert(symbol, move_down_pct, alert_level)

                except Exception as e:
                    dbg(f"Error processing {symbol} in monitor loop: {e}")
                    continue

            # Wait before next check
            time.sleep(MONITOR_INTERVAL)

    except KeyboardInterrupt:
        dbg("Monitor stopped by user")
    except Exception as e:
        dbg(f"Monitor error: {e}")
        send_error_to_telegram("Intraday Monitor Runtime Error", str(e))
    finally:
        # Send shutdown message
        end_msg = (
            f"🛑 *INTRADAY DOWNside MONITOR STOPPED*\n"
            f"_{get_ist_now().strftime('%A, %d %B %Y %I:%M %p IST')}_\n\n"
            f"Monitoring session ended. Review any open positions.\n\n"
            f"_Remember: Manual execution required for all trades_"
        )
        send_telegram(end_msg)
        dbg("=== INTRADAY DOWNside MONITOR STOPPED ===")


# ─────────────────────────────────────────────
# SCHEDULER FOR AUTOMATED SIGNAL GENERATION
# ─────────────────────────────────────────────
def run_scheduler():
    """Run scheduled signal generation at specific times"""
    dbg("=== STARTING SIGNAL SCHEDULER ===")

    last_run = {key: None for key in SIGNAL_INTERVALS.keys()}

    try:
        while True:
            now = get_ist_now()
            current_time = now.strftime("%H:%M")

            # Check if it's time to run any scheduled reports
            for report_type, time_str in SIGNAL_INTERVALS.items():
                if current_time == time_str and last_run[report_type] != current_time:
                    dbg(f"Time to run {report_type} report")

                    # Run the appropriate report function
                    if report_type == "morning":
                        run_morning_report()
                    elif report_type == "midday":
                        run_midday_report()
                    elif report_type == "evening":
                        run_evening_report()
                    elif report_type == "bond":
                        run_bond_focus_report()
                    elif report_type == "ipo":
                        run_ipo_closing_reminder()
                    elif report_type == "weekend":
                        run_weekend_tip()

                    last_run[report_type] = current_time

            # Sleep for a minute before checking again
            time.sleep(60)

    except KeyboardInterrupt:
        dbg("Scheduler stopped by user")
    except Exception as e:
        dbg(f"Scheduler error: {e}")
        send_error_to_telegram("Scheduler Runtime Error", str(e))


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Check credentials for core services
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GROQ_API_KEY]):
        print("[CRITICAL] Missing core environment variables:")
        print("  TELEGRAM_BOT_TOKEN=your_telegram_bot_token")
        print("  TELEGRAM_CHAT_ID=your_telegram_chat_id")
        print("  GROQ_API_KEY=your_groq_api_key")
        sys.exit(1)

    # Determine mode from command line argument
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    else:
        mode = "help"

    if mode == "morning":
        run_morning_report()
    elif mode == "bond":
        run_bond_focus_report()
    elif mode == "ipo":
        run_ipo_closing_reminder()
    elif mode == "midday":
        run_midday_report()
    elif mode == "evening":
        run_evening_report()
    elif mode == "weekend":
        run_weekend_tip()
    elif mode == "monitor":
        run_intraday_monitor()
    elif mode == "schedule":
        # Run both scheduler and monitor in parallel
        dbg("Starting scheduler and monitor in parallel...")

        # Start scheduler in background thread
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

        # Run monitor in main thread
        run_intraday_monitor()
    elif mode == "setup-broker":
        # Helper mode to test broker connection
        dbg("Testing broker connection...")
        if broker_feed:
            dbg(f"✓ Successfully connected to {type(broker_feed).__name__}")
            # Test fetching a sample price
            test_price = broker_feed.get_ltp("RELIANCE")
            if test_price > 0:
                dbg(f"✓ Sample LTP for RELIANCE: ₹{test_price}")
            else:
                dbg("✗ Could not fetch sample price")
        else:
            dbg("✗ No broker credentials found")
            print("\nTo set up broker connection, set these environment variables:")
            print("For Zerodha:")
            print("  ZERODHA_API_KEY=your_api_key")
            print("  ZERODHA_ACCESS_TOKEN=your_access_token")
            print("\nFor Dhan:")
            print("  DHAN_CLIENT_ID=your_client_id")
            print("  DHAN_ACCESS_TOKEN=your_access_token")
            print("\nFor Upstox:")
            print("  UPSTOX_API_KEY=your_api_key")
            print("  UPSTOX_ACCESS_TOKEN=your_access_token")
    elif mode == "help" or mode == "--help" or mode == "-h":
        print("""
LIVE TRADING INDIAN BOT WITH REAL-TIME MONITORING
=================================================

Modes of operation:

  python bot_live_trading.py morning    - Generate pre-market trading signals
  python bot_live_trading.py bond       - Generate bond investment analysis
  python bot_live_trading.py ipo        - Generate IPO closing reminders
  python bot_live_trading.py midday     - Generate midday market update
  python bot_ultimate_complete.py evening - Generate evening market review
  python bot_live_trading.py weekend    - Generate weekend preparation
  python bot_live_trading.py monitor    - Run LIVE intraday downside monitor (real broker data)
  python bot_live_trading.py schedule   - Run automated signal generation + live monitor
  python bot_live_trading.py setup-broker - Test broker connection

LIVE TRADING FEATURES:
• Real-time price data from Indian brokers (Zerodha Kite, Dhan, Upstox)
• Continuous monitoring for downside moves from day's open
• SEBI semi-automated compliant (AI advises, human executes)
• Risk management: Max 1.5% risk per trade, position sizing guidance
• Execution discipline: Exact entry zones, targets, stops, invalidation levels

SETUP FOR LIVE TRADING:
1. Set core environment variables:
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   GROQ_API_KEY=your_groq_api_key

2. Set broker credentials (choose one):
   # Zerodha Kite (Recommended)
   ZERODHA_API_KEY=your_zerodha_api_key
   ZERODHA_ACCESS_TOKEN=your_zerodha_access_token

   # OR Dhan
   DHAN_CLIENT_ID=your_dhan_client_id
   DHAN_ACCESS_TOKEN=your_dhan_access_token

   # OR Upstox
   UPSTOX_API_KEY=your_upstox_api_key
   UPSTOX_ACCESS_TOKEN=your_upstox_access_token

3. Optional: Set default broker
   DEFAULT_BROKER=zerodha  # or dhan or upstox

EXAMPLE USAGE:
  # Generate signals at market open:
    08:00 AM: python bot_live_trading.py morning
    08:05 AM: python bot_live_trading.py monitor   (or use schedule mode)
    12:00 PM: python bot_live_trading.py midday
    03:45 PM: python bot_live_trading.py evening

  # Test broker connection:
    python bot_live_trading.py setup-broker

IMPORTANT:
• This is educational content only - NOT SEBI registered advice
• You must manually place all orders through your broker
• Start with small position sizes (as suggested in signals)
• Past performance does not guarantee future results
• Always use stop losses and respect risk management guidelines
• For live trading, ensure you have valid broker API credentials
        """)
    else:
        print(f"Unknown mode: {mode}")
        print("Use 'help' to see available modes")
