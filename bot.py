import os
import sys
import time
import json
import requests
from datetime import datetime
import pytz

# ─────────────────────────────────────────────
# CREDENTIALS — loaded from environment variables (set as GitHub Secrets)
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GOOGLE_API_KEY     = os.environ.get("GOOGLE_API_KEY", "")

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# RETRY CONFIGURATION
# ─────────────────────────────────────────────
GEMINI_TIMEOUT      = 180          # seconds per attempt
GEMINI_MAX_RETRIES  = 3
GEMINI_RETRY_DELAY  = 15           # seconds between retries
TELEGRAM_TIMEOUT    = 30
TELEGRAM_MAX_RETRIES = 3
TELEGRAM_RETRY_DELAY = 5


def get_ist_now():
    return datetime.now(IST)


def dbg(msg: str):
    """Timestamped debug print to stdout (visible in GitHub Actions logs)."""
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
        f"_Check GitHub Actions logs for full traceback._"
    )
    dbg(f"Sending error notification to Telegram: {error[:120]}")
    send_telegram(msg, is_error=True)


# ─────────────────────────────────────────────
# GEMINI
# ─────────────────────────────────────────────
def call_gemini(prompt: str, context: str = "gemini_call") -> str:
    """
    Call Google Gemini with Google Search grounding.
    Retries up to GEMINI_MAX_RETRIES times with exponential-ish back-off.
    On complete failure, reports to Telegram and returns an error string.
    """
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
    )
    headers = {"Content-Type": "application/json"}
    body = {
        "contents":         [{"parts": [{"text": prompt}]}],
        "tools":            [{"google_search": {}}],
        "generationConfig": {"maxOutputTokens": 4096},
    }

    last_error = ""
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        dbg(f"Gemini API attempt {attempt}/{GEMINI_MAX_RETRIES} (timeout={GEMINI_TIMEOUT}s)...")
        try:
            r = requests.post(url, headers=headers, json=body, timeout=GEMINI_TIMEOUT)
            dbg(f"Gemini HTTP status: {r.status_code}")
            r.raise_for_status()
            data = r.json()

            # ── Debug: show finish reason and token counts ──
            for i, candidate in enumerate(data.get("candidates", [])):
                finish = candidate.get("finishReason", "UNKNOWN")
                dbg(f"Candidate {i}: finishReason={finish}")

            usage = data.get("usageMetadata", {})
            dbg(
                f"Tokens — prompt: {usage.get('promptTokenCount', '?')} | "
                f"candidates: {usage.get('candidatesTokenCount', '?')} | "
                f"total: {usage.get('totalTokenCount', '?')}"
            )

            # ── Extract text parts ──
            text_parts = []
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        text_parts.append(part["text"])

            result = "\n".join(text_parts).strip()
            if not result:
                raise ValueError("Empty text response from Gemini — possible content filter or quota issue.")

            dbg(f"Gemini returned {len(result)} characters.")
            return result

        except requests.exceptions.Timeout:
            last_error = f"Request timed out after {GEMINI_TIMEOUT}s"
            dbg(f"[WARN] {last_error}")
        except requests.exceptions.HTTPError as e:
            last_error = f"HTTP {r.status_code}: {r.text[:300]}"
            dbg(f"[WARN] Gemini HTTP error: {last_error}")
        except Exception as e:
            last_error = str(e)
            dbg(f"[WARN] Gemini error: {last_error}")

        if attempt < GEMINI_MAX_RETRIES:
            wait = GEMINI_RETRY_DELAY * attempt   # 15s, 30s, 45s ...
            dbg(f"Retrying Gemini in {wait}s...")
            time.sleep(wait)

    # ── All retries exhausted ──
    err_msg = f"Gemini failed after {GEMINI_MAX_RETRIES} attempts. Last error: {last_error}"
    dbg(f"[ERROR] {err_msg}")
    send_error_to_telegram(context, err_msg)
    return f"[ERROR] {err_msg}"


# ─────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────
def build_prompt(report_type: str) -> str:
    now       = get_ist_now()
    date_str  = now.strftime("%A, %d %B %Y")
    time_str  = now.strftime("%I:%M %p IST")
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    open_mins = max(0, int((market_open - now).total_seconds() // 60))

    if report_type == "morning":
        return f"""
You are an elite Indian stock market analyst. Today is {date_str}, {time_str}.
Indian market opens at 9:15 AM IST ({open_mins} minutes from now).

YOUR MISSION:
Scan the entire Indian market and pick the best stocks for today.
Do NOT wait to be told which stocks — find them yourself using web search.

MANDATORY RESEARCH BEFORE ANSWERING:
1. Search: "GIFT Nifty today {date_str}" — get pre-market direction
2. Search: "US stock market closing {date_str}" — Dow, S&P, NASDAQ
3. Search: "crude oil price today" — impact on Indian market
4. Search: "USD INR today" — rupee strength
5. Search: "India stock market news today {date_str}" — top stories
6. Search: "NSE BSE top gainers losers today" — momentum stocks
7. Search: "FII DII data today India" — institutional activity
8. Search: "Indian stocks results announcement today" — earnings catalysts
9. Search: "top intraday stocks NSE today {date_str}"
10. Search: "best fundamentally strong Indian stocks 2025 long term"
11. Search: "IPO open today India {date_str}" — currently open IPOs
12. Search: "IPO allotment today India {date_str}" — allotment results
13. Search: "IPO GMP today India grey market premium {date_str}"
14. Search: "FPO open today India {date_str}" — currently open FPOs
15. Search: "upcoming IPO India next 30 days {date_str}"
16. Search: "IPO subscription status today India {date_str}" — how many times oversubscribed
17. Search: "IPO listing today NSE BSE {date_str}" — listings happening today
18. Search: "IPO allotment probability retail HNI QIB {date_str}"

INTRADAY PICKS (pick TOP 5):
Select stocks that have a clear catalyst TODAY:
- Major news / earnings / announcement
- High liquidity (Nifty 50, Nifty Next 50, Midcap 150 preferred)
- Strong pre-market momentum
- Clear support/resistance levels
- FII/DII institutional buying

LONG TERM PICKS (pick TOP 3):
Select stocks that pass CA Rachana Ranade's framework:
- Debt:Equity below 1 (ideally debt-free)
- ROCE above 15%
- Positive free cash flow 3+ years
- Promoter holding 40%+ with no pledging
- P/E below industry average
- Reserves & Surplus growing 5 years
- Industry tailwind or monopoly position

OUTPUT FORMAT — use EXACTLY this Telegram markdown format:

🌅 *STOCK ALERTS — {date_str}*
_{time_str} | Market opens in {open_mins} mins_

━━━━━━━━━━━━━━━━━━━━
🌍 *GLOBAL MARKET PULSE*
━━━━━━━━━━━━━━━━━━━━
• GIFT Nifty: [value and direction]
• US Markets: [Dow, S&P, NASDAQ closing]
• Crude Oil: [price and India impact]
• USD/INR: [rate and impact]
• Overall Sentiment: 🟢 BULLISH or 🔴 BEARISH or 🟡 MIXED

━━━━━━━━━━━━━━━━━━━━
⚡ TOP 5 INTRADAY PICKS
━━━━━━━━━━━━━━━━━━━━

1️⃣ *STOCK NAME (SYMBOL)*
   Sector: [sector]
   Action: 🟢 BUY or 🔴 SELL
   Entry: ₹[xxx] - ₹[xxx]
   Target: ₹[xxx] (+[x]%)
   Stop Loss: ₹[xxx] (-[x]%)
   Why today: [1-2 lines — specific news or catalyst]
   Risk: [LOW/MEDIUM/HIGH]

2️⃣ [same format]
3️⃣ [same format]
4️⃣ [same format]
5️⃣ [same format]

━━━━━━━━━━━━━━━━━━━━
📈 TOP 3 LONG TERM PICKS
━━━━━━━━━━━━━━━━━━━━

1️⃣ *STOCK NAME (SYMBOL)*
   Sector: [sector]
   Action: 💎 BUY or ➕ ACCUMULATE
   Price Zone: ₹[xxx]
   Target 1yr: ₹[xxx] (+[x]%)
   Target 3yr: ₹[xxx] (+[x]%)
   Fundamental Score: [x]/100
   Why: [2-3 lines on fundamentals]
   Key Risk: [1 line]

2️⃣ [same format]
3️⃣ [same format]

━━━━━━━━━━━━━━━━━━━━
🔥 SECTOR IN FOCUS TODAY
━━━━━━━━━━━━━━━━━━━━
[Sector]: [Why this sector is hot today]

━━━━━━━━━━━━━━━━━━━━
🚫 AVOID TODAY
━━━━━━━━━━━━━━━━━━━━
• [Stock]: [reason]
• [Stock]: [reason]
• [Stock]: [reason]

━━━━━━━━━━━━━━━━━━━━
🏷️ IPO / FPO INTELLIGENCE — {date_str}
━━━━━━━━━━━━━━━━━━━━

📂 CURRENTLY OPEN IPOs / FPOs:
(List ALL open issues. If none open, state "No IPO/FPO open today".)

For EACH open IPO / FPO use this block:

🔷 *[COMPANY NAME] IPO* (or FPO)
   Type: [Mainboard / SME / FPO]
   Issue Price: ₹[xxx] - ₹[xxx per share]
   Lot Size: [x shares per lot]
   Issue Size: ₹[xxx Crores]
   Open: [date] → Close: [date]
   Category Subscription (latest):
     • Retail (RII): [x.xx]x subscribed
     • Non-Institutional (NII/HNI): [x.xx]x
     • QIB: [x.xx]x
     • Total: [x.xx]x
   GMP (Grey Market Premium): ₹[xxx] ([+x]% over issue price)
   Estimated Listing Gain: [x]% above / below issue price

   📊 FUNDAMENTALS SNAPSHOT:
   • Business: [1 line what the company does]
   • Revenue Growth (3yr): [x]% CAGR
   • Profit/Loss: [profitable or loss-making]
   • Debt:Equity: [ratio]
   • P/E vs Peers: [under/fairly/overvalued]
   • Promoter holding post-IPO: [x]%
   • Verdict: ✅ APPLY / ⚠️ RISKY / ❌ AVOID — [1 line reason]

   🎯 ALLOTMENT PROBABILITY & STRATEGY:
   • Retail allotment chance: [Low/Medium/High] — approx [x]%
   • Apply from MULTIPLE demat accounts (family members)
   • ALWAYS apply at CUT-OFF PRICE
   • Apply for EXACTLY 1 LOT per account
   • Approve UPI mandate IMMEDIATELY

   🧮 LOT & PROFIT CALCULATION:
   • 1 lot = [x shares] × ₹[issue price] = ₹[total investment per lot]
   • If allotted at GMP-based listing: Profit = ₹[xxx] ([x]% gain)
   • Allotment date: [date] | Listing date: [date]
   • Registrar: [name] — check at [URL]

📅 UPCOMING IPOs — NEXT 30 DAYS:
| Company | Open | Close | Est. Size | Type | Watchlist? |
|---------|------|-------|-----------|------|------------|
| [Name]  | [dt] | [dt]  | ₹[x Cr]   | [MB/SME] | ✅/⚠️/❌ |

🏆 LISTINGS TODAY:
• *[COMPANY]* — Issue price ₹[x] | Expected listing ₹[x] ([+/-x]%)
  GMP: ₹[x] | Action: SELL AT OPEN / HOLD / AVOID BUYING TODAY

━━━━━━━━━━━━━━━━━━━━
💡 TODAY'S MARKET MANTRA
━━━━━━━━━━━━━━━━━━━━
[One sharp market insight for today in 1-2 sentences]

_⚠️ For education only. Not SEBI registered advice. Invest at your own risk._
"""

    elif report_type == "midday":
        return f"""
You are an elite Indian stock market analyst. Time is {time_str}, {date_str}.
Indian market is LIVE right now (9:15 AM - 3:30 PM IST).

Do a midday market check. Search for:
1. "NSE top gainers today" and "NSE top losers today"
2. "Nifty 50 today performance {date_str}"
3. "Indian stock market news afternoon {date_str}"
4. Any breaking news affecting stocks right now

Send a SHORT midday update in this format:

📊 *MIDDAY UPDATE — {date_str}*
_{time_str}_

🟢 *TOP GAINERS RIGHT NOW*
• [Stock]: +[x]% — [1 line why]
• [Stock]: +[x]%
• [Stock]: +[x]%

🔴 *TOP LOSERS RIGHT NOW*
• [Stock]: -[x]% — [1 line why]
• [Stock]: -[x]%

📰 *BREAKING NEWS AFFECTING MARKET*
• [News item and which stocks it impacts]

⚡ *INTRADAY ACTION STILL VALID?*
[Quick update on morning picks — still hold or exit?]

_Market closes at 3:30 PM IST_
"""

    elif report_type == "evening":
        return f"""
You are an elite Indian stock market analyst. Time is {time_str}, {date_str}.
Indian market has CLOSED for today.

Search for:
1. "NSE BSE market closing today {date_str}"
2. "Nifty Sensex closing today"
3. "Indian stock market highlights {date_str}"
4. "Tomorrow Indian market outlook"

Send evening summary in this format:

🌆 *MARKET CLOSE SUMMARY — {date_str}*
_{time_str}_

📊 *TODAY'S SCORECARD*
• Nifty 50: [closing value] ([+/-x]%)
• Sensex: [closing value] ([+/-x]%)
• Market breadth: [advances vs declines]
• FII today: [net buy/sell in crores]
• DII today: [net buy/sell in crores]

🏆 *BEST PERFORMERS TODAY*
• [Stock]: +[x]% — [reason]
• [Stock]: +[x]%
• [Stock]: +[x]%

📉 *BIGGEST LOSERS TODAY*
• [Stock]: -[x]% — [reason]
• [Stock]: -[x]%

🔭 *TOMORROW'S OUTLOOK*
[What to expect tomorrow — global cues, upcoming results, events]

💎 *LONG TERM OPPORTUNITY SPOTTED*
[Any stock that fell today but fundamentally strong = buying opportunity?]

🌙 *GOOD NIGHT NOTE*
[One motivational/educational investing insight]

_⚠️ For education only. Not SEBI registered advice._
"""

    return ""


def build_ipo_closing_prompt() -> str:
    now      = get_ist_now()
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M %p IST")
    return f"""
You are an expert Indian IPO analyst. Today is {date_str}, {time_str}.

Search for:
1. "IPO closing today India {date_str}"
2. "IPO last day to apply {date_str} India"
3. "IPO subscription status today {date_str}"
4. "IPO GMP today grey market premium {date_str}"

ONLY send this message if at least 1 IPO/FPO closes TODAY.
If no IPO closes today, reply with exactly: NO_IPO_CLOSING_TODAY

If IPOs are closing today, use this format:

⏰ *LAST CHANCE — IPO CLOSING TODAY!*
_{time_str} | {date_str}_

For EACH IPO closing today:

🔔 *[COMPANY NAME] IPO — CLOSES TODAY {date_str}*
   Issue Price: ₹[xxx] | Lot: [x shares] = ₹[investment/lot]
   Current Subscription: Retail [x]x | HNI [x]x | QIB [x]x | Total [x]x
   GMP Right Now: ₹[xxx] ([+x]% profit per lot if listed at GMP)

   ✅ SHOULD YOU APPLY NOW?
   Verdict: [APPLY / RISKY / SKIP] — [1-2 line reason]

   ⚡ QUICK ALLOTMENT GUIDE:
   • Apply at CUT-OFF price — mandatory
   • 1 lot per account (= [x shares] = ₹[amount blocked])
   • UPI mandate approval deadline: 5:00 PM today — DO NOT MISS

   💰 PROFIT ESTIMATE IF ALLOTTED:
   • At GMP listing: +₹[xxx] per lot ([x]%)
   • Sell on listing day? [YES/NO + reason]

   📅 KEY DATES:
   • Allotment: [date] | Listing: [date] on [NSE/BSE]
   • Registrar: [name] — check at [URL]

_⚠️ Apply only if fundamentals + GMP support it. Not SEBI registered advice._
"""


def build_weekend_prompt() -> str:
    now = get_ist_now()
    return f"""
Today is {now.strftime('%A, %d %B %Y')}. Indian markets are closed on weekends.

Search for:
1. "Indian stock market weekly analysis {now.strftime('%B %Y')}"
2. "Best stocks to watch next week India"
3. "Global events next week affecting Indian market"
4. "IPO open this week India"
5. "Upcoming IPO India next 30 days {now.strftime('%B %Y')}"
6. "IPO GMP today India grey market premium"
7. "IPO allotment result this week India"
8. "IPO listing next week NSE BSE"

Send a weekend investor update in this format:

🗓️ *WEEKEND INVESTOR CORNER*
_{now.strftime('%A, %d %B %Y')}_

📚 *THIS WEEK IN MARKETS*
[3-4 bullet summary of the week]

🔭 *STOCKS TO RESEARCH THIS WEEKEND*
• [Stock]: [why to research it]
• [Stock]: [why to research it]
• [Stock]: [why to research it]

🌍 *GLOBAL EVENTS NEXT WEEK TO WATCH*
• [Event and market impact]
• [Event and market impact]

━━━━━━━━━━━━━━━━━━━━
🏷️ WEEKEND IPO / FPO CORNER
━━━━━━━━━━━━━━━━━━━━

📂 IPOs / FPOs OPEN RIGHT NOW:
For each open issue:
🔷 *[COMPANY NAME] IPO/FPO*
   Issue Price: ₹[xxx] | Lot: [x shares] | Close: [date]
   Subscription: Retail [x]x | HNI [x]x | QIB [x]x
   GMP: ₹[xxx] ([+x]% over issue price)
   Verdict: ✅ APPLY / ⚠️ RISKY / ❌ AVOID
   Allotment: [date] | Listing: [date]
   Check: [registrar URL]

📅 IPO CALENDAR — NEXT 4 WEEKS:
| Company | Open | Close | Size | Type | Verdict |
|---------|------|-------|------|------|---------|
| [Name]  | [dt] | [dt]  | ₹[x Cr] | [MB/SME] | ✅/⚠️/❌ |

🏆 LISTINGS NEXT WEEK:
• *[Company]* — Listing [date] | Issue ₹[x] | GMP ₹[x] → Est. ₹[x] ([+x]%)
  Action: SELL AT OPEN / HOLD / PARTIAL SELL

💡 *WEEKEND LEARNING*
[One fundamental analysis concept explained simply]

_Markets reopen Monday 9:15 AM IST_ 🔔
"""


# ─────────────────────────────────────────────
# REPORT RUNNERS
# ─────────────────────────────────────────────
def run_morning_report():
    now = get_ist_now()
    if now.weekday() >= 5:
        dbg("Weekend — skipping morning report.")
        return
    dbg("=== STARTING MORNING REPORT ===")
    send_telegram("🔄 _Scanning markets, searching news, building your report... please wait ~60 seconds..._")
    result = call_gemini(build_prompt("morning"), context="morning_report")
    if result.startswith("[ERROR]"):
        dbg("Morning report failed — error already sent to Telegram.")
        return
    dbg(f"Morning report ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== MORNING REPORT DONE ===")


def run_ipo_closing_reminder():
    now = get_ist_now()
    if now.weekday() >= 5:
        dbg("Weekend — skipping IPO closing reminder.")
        return
    dbg("=== CHECKING IPO CLOSING REMINDER ===")
    result = call_gemini(build_ipo_closing_prompt(), context="ipo_closing")
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
    result = call_gemini(build_prompt("midday"), context="midday_report")
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
    result = call_gemini(build_prompt("evening"), context="evening_report")
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
    result = call_gemini(build_weekend_prompt(), context="weekend_tip")
    if result.startswith("[ERROR]"):
        dbg("Weekend tip failed — error already sent to Telegram.")
        return
    dbg(f"Weekend tip ready — {len(result)} chars. Sending to Telegram...")
    send_telegram(result)
    dbg("=== WEEKEND TIP DONE ===")


# ─────────────────────────────────────────────
# ENTRY POINT — called by GitHub Actions
# e.g.  python bot.py morning
# ─────────────────────────────────────────────
if __name__ == "__main__":
    report_type = sys.argv[1] if len(sys.argv) > 1 else "morning"
    dbg(f"Bot started — report_type='{report_type}'")

    # Basic credential check
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT_ID,
        "GOOGLE_API_KEY":     GOOGLE_API_KEY,
    }.items() if not v]
    if missing:
        print(f"[CRITICAL] Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    dispatch = {
        "morning": run_morning_report,
        "ipo":     run_ipo_closing_reminder,
        "midday":  run_midday_report,
        "evening": run_evening_report,
        "weekend": run_weekend_tip,
    }
    fn = dispatch.get(report_type)
    if fn:
        fn()
    else:
        msg = f"Unknown report type: '{report_type}'. Valid: {list(dispatch.keys())}"
        dbg(f"[ERROR] {msg}")
        print(msg)
        sys.exit(1)

    dbg("Bot finished successfully.")
