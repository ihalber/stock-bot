import os
import json
import requests
import yfinance as yf
from anthropic import Anthropic
from twilio.rest import Client
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
FINNHUB_API_KEY   = os.environ["FINNHUB_API_KEY"]
TWILIO_SID        = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH       = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM       = os.environ["TWILIO_WHATSAPP_FROM"]
WHATSAPP_TO       = os.environ["WHATSAPP_TO"]

# Candidate stocks to screen (mix of ASX and US)
ASX_TICKERS = [
    "CBA.AX", "BHP.AX", "CSL.AX", "NAB.AX", "WBC.AX",
    "ANZ.AX", "WES.AX", "MQG.AX", "RIO.AX", "TLS.AX",
    "FMG.AX", "WOW.AX", "GMG.AX", "REA.AX", "XRO.AX"
]
US_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM", "V", "UNH",
    "MA", "HD", "PG", "JNJ", "AVGO"
]
ALL_TICKERS = ASX_TICKERS + US_TICKERS


# ── Data Fetching ─────────────────────────────────────────────────────────────
def get_stock_data(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo")
        if hist.empty:
            return None

        close = hist["Close"]
        current_price = round(float(close.iloc[-1]), 2)

        sma20  = round(float(close.rolling(20).mean().iloc[-1]), 2)
        sma50  = round(float(close.rolling(50).mean().iloc[-1]), 2)
        change_1w = round((close.iloc[-1] / close.iloc[-5] - 1) * 100, 2) if len(close) >= 5  else None
        change_1m = round((close.iloc[-1] / close.iloc[-21] - 1) * 100, 2) if len(close) >= 21 else None

        delta  = close.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        rs     = gain / loss
        rsi    = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        info = t.info
        pe     = info.get("trailingPE")
        pb     = info.get("priceToBook")
        mktcap = info.get("marketCap")
        sector = info.get("sector", "Unknown")
        name   = info.get("longName", ticker)

        return {
            "ticker": ticker,
            "name": name,
            "sector": sector,
            "price": current_price,
            "sma20": sma20,
            "sma50": sma50,
            "rsi": rsi,
            "change_1w_pct": change_1w,
            "change_1m_pct": change_1m,
            "pe_ratio": round(pe, 1) if pe else None,
            "pb_ratio": round(pb, 2) if pb else None,
            "market_cap_bn": round(mktcap / 1e9, 1) if mktcap else None,
        }
    except Exception as e:
        print("  Warning: could not fetch {}: {}".format(ticker, e))
        return None


def get_news(ticker):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        url = (
            "https://finnhub.io/api/v1/company-news"
            "?symbol={}&from={}&to={}&token={}".format(
                ticker.replace(".AX", ""), week_ago, today, FINNHUB_API_KEY
            )
        )
        resp = requests.get(url, timeout=10)
        articles = resp.json()
        return [a["headline"] for a in articles[:3]] if isinstance(articles, list) else []
    except Exception:
        return []


# ── Claude Analysis ───────────────────────────────────────────────────────────
def ask_claude(stocks_data):
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    stocks_json = json.dumps(stocks_data, indent=2)
    today_str   = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y")

    prompt = """Today is {}. You are a professional stock analyst.

Below is fundamental, technical, and news data for {} stocks across ASX and US markets.

Your task:
1. Analyse all stocks holistically — technicals, fundamentals, and news sentiment.
2. Select the TOP 5 stocks with the best risk/reward opportunity TODAY.
3. For each pick, write 2-3 sentences explaining WHY (mention specific data points).
4. Rate each pick: Strong Buy / Buy / Speculative.

Return your response in this exact format:

DAILY TOP 5 STOCK PICKS — {}

1. [TICKER] — [Company Name] ([Rating])
   Reasoning: [Your 2-3 sentence reasoning]
   Price: $[X] | RSI: [X] | P/E: [X] | 1W: [X]%

2. ...
3. ...
4. ...
5. ...

This is AI-generated analysis, not financial advice. Always do your own research.

STOCK DATA:
{}
""".format(today_str, len(stocks_data), today_str, stocks_json)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ── WhatsApp Delivery ─────────────────────────────────────────────────────────
def send_whatsapp(message):
    twilio = Client(TWILIO_SID, TWILIO_AUTH)
    # Split into chunks of max 1500 chars at a newline boundary
    chunks = []
    while len(message) > 1500:
        split_at = message.rfind("\n", 0, 1500)
        if split_at == -1:
            split_at = 1500
        chunks.append(message[:split_at].strip())
        message = message[split_at:].strip()
    chunks.append(message)

    for i, chunk in enumerate(chunks):
        twilio.messages.create(
            from_=TWILIO_FROM,
            to=WHATSAPP_TO,
            body=chunk
        )
        print("WhatsApp message {} of {} sent.".format(i + 1, len(chunks)))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Starting stock screener — {}".format(datetime.now().strftime("%Y-%m-%d %H:%M")))

    stocks_data = []
    for ticker in ALL_TICKERS:
        print("  Fetching {}...".format(ticker))
        data = get_stock_data(ticker)
        if data:
            data["recent_news"] = get_news(ticker)
            stocks_data.append(data)

    print("\nGot data for {} stocks. Asking Claude...".format(len(stocks_data)))

    recommendations = ask_claude(stocks_data)
    print("\n" + recommendations)

    send_whatsapp(recommendations)


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "web"
    if mode == "cron":
        main()
    else:
        port = int(os.environ.get("PORT", 8080))
        print("Starting Flask on port {}".format(port))
        app.run(host="0.0.0.0", port=port)
