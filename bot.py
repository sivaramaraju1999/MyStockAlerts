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
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")

IST = pytz.timezone("Asia/Kolkata")

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
                "content": "You are an elite Indian financial market data intelligence AI assistant with comprehensive access to live global market summaries, NSE/BSE analytics, and IPO metrics."
            },
            {
                "role": "user", 
                "content": prompt
            }
        ],
        "temperature": 0.2,
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
Scan the entire Indian market and pick the best stocks for today based on latest global signals and domestic parameters.

MANDATORY DATA EVALUATION BEFORE ANSWERING:
1. Pre-market directions: "GIFT Nifty today {date_str}"
2. Global Closures: "US stock market closing {date_str}" — Dow, S&P, NASDAQ
3. Core Variables: Crude oil price trends, USD/INR conversion impact on India.
4. Active Domestic Triggers: "India stock market news today {date_str}", "NSE BSE top gainers losers", "FII DII net data".
5. IPO Ecosystem metrics: Current Open issues, allotments happening today, and active Grey Market Premiums (GMP).

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

Evaluate the following dynamic midday metrics:
1. Top real-time gainers and losers right now on the NSE.
2. Nifty 50 absolute midday variance and baseline performance trends for {date_str}.
3. Breaking afternoon news updates that are currently pushing momentum stocks.

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

Synthesize market close profiles:
1. Closing figures for Nifty 50, Sensex, and structural market breadth data.
2. Net institutional turnover updates (FII / DII provisional transaction metrics).
3. Strategic projections for tomorrow's opening based on current closing momentum.

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

Verify active Indian Mainboard and SME IPO issues reaching their closing window today ({date_str}). Consolidate final oversubscription multiples, live GMP metrics, and direct execution strategies.

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

Formulate an educational macro weekly performance recap including:
1. Strategic summary of the past trading week's movements across major indices.
2. Next week's key earnings calendars, IPO schedules, and global economic catalysts.

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
    send_telegram("🔄 _Scanning markets, aggregating vectors, preparing your Groq Llama 3.3 financial advisory report..._")
    result = call_groq(build_prompt("morning"), context="morning_report")
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
    result = call_groq(build_weekend_prompt(), context="weekend_tip")
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
        "GROQ_API_KEY":       GROQ_API_KEY,
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
