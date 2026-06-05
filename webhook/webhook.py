import os
import json
from datetime import datetime
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

load_dotenv()

PORTFOLIO_FILE = "portfolio.json"

app = Flask(__name__)


# ── Portfolio Management ──────────────────────────────────────────────────────
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    return {}


def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


def portfolio_add(ticker, buy_price):
    portfolio = load_portfolio()
    ticker = ticker.upper()
    portfolio[ticker] = {
        "buy_price": buy_price,
        "date_added": datetime.now().strftime("%Y-%m-%d")
    }
    save_portfolio(portfolio)
    return ticker


def portfolio_remove(ticker):
    portfolio = load_portfolio()
    ticker = ticker.upper()
    if ticker in portfolio:
        del portfolio[ticker]
        save_portfolio(portfolio)
        return True
    return False


def portfolio_summary():
    portfolio = load_portfolio()
    if not portfolio:
        return "Your portfolio is empty. Use 'bought TICKER PRICE' to add a stock."
    lines = ["*Your Portfolio:*"]
    for ticker, info in portfolio.items():
        lines.append("  • {} — bought at ${} on {}".format(
            ticker, info["buy_price"], info["date_added"]
        ))
    return "\n".join(lines)


# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    incoming = request.form.get("Body", "").strip()
    parts = incoming.lower().split()
    resp = MessagingResponse()

    if not parts:
        resp.message("Commands:\n• bought TICKER PRICE\n• sold TICKER\n• portfolio")
        return Response(str(resp), mimetype="text/xml")

    command = parts[0]

    # bought AAPL 150
    if command == "bought" and len(parts) >= 3:
        try:
            ticker = parts[1].upper()
            price = float(parts[2])
            portfolio_add(ticker, price)
            resp.message("✅ Added {} to your portfolio at ${}.".format(ticker, price))
        except ValueError:
            resp.message("❌ Invalid price. Usage: bought AAPL 150.00")

    # sold AAPL
    elif command == "sold" and len(parts) >= 2:
        ticker = parts[1].upper()
        removed = portfolio_remove(ticker)
        if removed:
            resp.message("✅ Removed {} from your portfolio.".format(ticker))
        else:
            resp.message("❌ {} not found in your portfolio.".format(ticker))

    # portfolio
    elif command == "portfolio":
        resp.message(portfolio_summary())

    else:
        resp.message("Commands:\n• bought TICKER PRICE\n• sold TICKER\n• portfolio")

    return Response(str(resp), mimetype="text/xml")


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("Starting Flask webhook on port {}".format(port))
    app.run(host="0.0.0.0", port=port)
