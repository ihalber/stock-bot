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

PORTFOLIO_FILE = "portfolio.json"

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


# ── Portfolio Management ──────────────────────────────────────────────────────
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    return {}


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
def ask_claude(stocks_data, portfolio=None):
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    stocks_json = json.dumps(stocks_data, indent=2)
    today_str   = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y")

    # Build portfolio section if holdings exist
    portfolio_section = ""
    if portfolio:
        holdings = []
        for ticker, info in portfolio.items():
            match = next((s for s in stocks_data if s["ticker"] == ticker), None)
            current_price = match["price"] if match else "N/A"
            buy_price = info["buy_price"]
            if current_price != "N/A":
                gain_pct = round((current_price / buy_price - 1) * 100, 2)
                holdings.append({
                    "ticker": ticker,
                    "buy_price": buy_price,
                    "current_price": current_price,
                    "gain_loss_pct": gain_pct,
                    "date_added": info["date_added"]
                })
            else:
                holdings.append({
                    "ticker": ticker,
                    "buy_price": buy_price,
                    "current_price": "unavailable",
                    "date_added": info["date_added"]
                })

        portfolio_json = json.dumps(holdings, indent=2)
        portfolio_section = """
PORTFOLIO REVIEW:
The user currently holds these stocks. For each one, provide a clear SELL, HOLD, or AVERAGE DOWN recommendation with reasoning based on current technicals, fundamentals, news, and their gain/loss position.

Format each as:
[TICKER] — SELL / HOLD / AVERAGE DOWN
Reasoning: [2-3 sentences with specific data points]
Bought: $[buy_price] | Now: $[current_price] | P&L: [X]%

HOLDINGS:
{}
""".format(portfolio_json)

    prompt = """Today is {}. You are a professional stock analyst.

Below is fundamental, technical, and news data for {} stocks across ASX and US markets.

{}

YOUR TASKS:

{}

TOP 5 NEW PICKS:
Analyse all stocks holistically — technicals, fundamentals, and news sentiment.
Select the TOP 5 stocks with the best risk/reward opportunity TODAY.
For each pick, write 2-3 sentences explaining WHY (mention specific data points).
Rate each pick: Strong Buy / Buy / Speculative.

Return your response in this exact format:

{}DAILY TOP 5 STOCK PICKS — {}

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
""".format(
        today_str,
        len(stocks_data),
        portfolio_section,
        "1. PORTFOLIO REVIEW (see holdings above)\n2. " if portfolio else "1. ",
        "📊 PORTFOLIO REVIEW\n\n[Portfolio recommendations here]\n\n---\n\n" if portfolio else "",
        today_str,
        stocks_json
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ── WhatsApp Delivery ─────────────────────────────────────────────────────────
def send_whatsapp(message):
    twilio = Client(TWILIO_SID, TWILIO_AUTH)
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

    portfolio = load_portfolio()

    # Fetch data for all candidate tickers + any portfolio stocks not in the list
    tickers_to_fetch = list(ALL_TICKERS)
    for ticker in portfolio:
        if ticker not in tickers_to_fetch:
            tickers_to_fetch.append(ticker)

    stocks_data = []
    for ticker in tickers_to_fetch:
        print("  Fetching {}...".format(ticker))
        data = get_stock_data(ticker)
        if data:
            data["recent_news"] = get_news(ticker)
            stocks_data.append(data)

    print("\nGot data for {} stocks. Asking Claude...".format(len(stocks_data)))

    recommendations = ask_claude(stocks_data, portfolio if portfolio else None)
    print("\n" + recommendations)

    send_whatsapp(recommendations)


if __name__ == "__main__":
    main()
