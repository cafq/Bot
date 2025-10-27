import ccxt
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
import pytz
import os
from flask import Flask
import threading

# =============================
# 🔑 CONFIG
# =============================
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"

# Canaux Telegram
CHAT_CRYPTO = "@TradeSignalAI"
CHAT_FOREX  = "@TradeForexIA"
CHAT_ACTIONS = "@TradeStocksAI"

# Actifs suivis
SYMBOLS_CRYPTO = ["BTC/USDT", "SOL/USDT", "ETH/USDT"]
SYMBOLS_FOREX  = ["EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD"]
SYMBOLS_ACTIONS = ["AAPL", "TSLA", "NVDA", "SPY"]

# Seuils de variation (pour alertes prix)
THRESHOLD_CRYPTO = 0.017  # ±1.7%
THRESHOLD_FOREX  = 0.003  # ±0.3%
THRESHOLD_ACTIONS = 0.008 # ±0.8%

# Délai entre chaque vérification
INTERVAL = 300  # 5 minutes
TIMEFRAME = "4h"
LIMIT = 150
TZ = pytz.timezone("Europe/Paris")

# Alpha Vantage Key
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "LBWF1IZP1S5L6X2W")

# =============================
# 📡 EXCHANGES
# =============================
exchange = ccxt.kraken()

# Mémoires
last_signals = {}
last_prices = {}

# =============================
# 📤 TELEGRAM
# =============================
def send_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": text})

# =============================
# 📐 INDICATEURS
# =============================
def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line

# =============================
# 🔍 ANALYSE
# =============================
def analyze(df):
    close = df["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi_v = rsi(close, 21)
    macd_line, signal_line, hist = macd(close)

    latest = {
        "close": float(close.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "rsi": float(rsi_v.iloc[-1]),
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
    }

    signals = []
    ema_dir = "BUY" if latest["ema20"] > latest["ema50"] else "SELL"
    macd_dir = "BUY" if latest["macd"] > latest["signal"] else "SELL"

    # EMA cross
    if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
        signals.append("EMA — BUY")
    if ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
        signals.append("EMA — SELL")

    # RSI
    if rsi_v.iloc[-2] < 30 and rsi_v.iloc[-1] >= 30:
        signals.append("RSI — BUY")
    if rsi_v.iloc[-2] > 72 and rsi_v.iloc[-1] < 68:
        signals.append("RSI — SELL")

    # MACD cross
    if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append("MACD — BUY")
    if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append("MACD — SELL")

    # Double signal
    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

# =============================
# 📊 FETCH DATA
# =============================
def fetch_crypto(sym):
    ohlcv = exchange.fetch_ohlcv(sym, "4h", limit=LIMIT)
    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df

def fetch_alpha_vantage(symbol, fx=False):
    if fx:
        from_sym, to_sym = symbol.split("/")
        url = f"https://www.alphavantage.co/query?function=FX_INTRADAY&from_symbol={from_sym}&to_symbol={to_sym}&interval=60min&outputsize=full&apikey={ALPHAVANTAGE_KEY}"
        key = "Time Series FX (60min)"
    else:
        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol={symbol}&interval=60min&outputsize=full&apikey={ALPHAVANTAGE_KEY}"
        key = "Time Series (60min)"

    r = requests.get(url, timeout=30)
    data = r.json()
    if key not in data:
        raise Exception(f"AlphaVantage error for {symbol}: {data}")
    ts = data[key]
    df = pd.DataFrame([
        {
            "time": pd.to_datetime(t),
            "open": float(v["1. open"]),
            "high": float(v["2. high"]),
            "low": float(v["3. low"]),
            "close": float(v["4. close"]),
        } for t, v in ts.items()
    ])
    df = df.sort_values("time").reset_index(drop=True)
    df = df.set_index("time").resample("4h").agg({
        "open":"first","high":"max","low":"min","close":"last"
    }).dropna().reset_index()
    return df.tail(LIMIT)

# =============================
# 📩 ENVOI SIGNALS
# =============================
def send_signal(sym, chat, threshold, source):
    try:
        df = fetch_crypto(sym) if source == "crypto" else fetch_alpha_vantage(sym, fx=(source=="forex"))
        latest, signals = analyze(df)
        current_price = latest["close"]
        key = f"{chat}_{sym}"
        last_signal = last_signals.get(key)
        last_price = last_prices.get(key)
        prefix = ""
        send = False

        if signals and (last_signal is None or signals != last_signal):
            send = True
            last_prices[key] = current_price

        elif last_price is not None and last_signal:
            change = (current_price - last_price) / last_price
            if abs(change) >= threshold:
                pct = change * 100
                if pct >= 0:
                    prefix = f"✅ {sym} a augmenté de +{pct:.2f}% depuis le dernier signal.\n\n"
                else:
                    prefix = f"⚠️ {sym} a baissé de {pct:.2f}% depuis le dernier signal.\n\n"
                send = True
                last_prices[key] = current_price

        if send:
            msg = f"""{prefix}📊 {sym} — ({TIMEFRAME})

📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20'] > latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.2f}) {'>' if latest['macd'] > latest['signal'] else '<'} Signal ({latest['signal']:.2f})
💪 RSI : {latest['rsi']:.1f}

⚡ Signal Global → {' / '.join(signals)}
💰 Prix : {latest['close']:.2f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(chat, msg)
            last_signals[key] = signals
            print(msg)

    except Exception as e:
        print(f"Erreur {sym} ({source}) : {e}")

# =============================
# 🚀 BOUCLE PRINCIPALE
# =============================
def loop():
    # Message initial
    print("🚀 Démarrage du bot et envoi des signaux initiaux...")
    for (symbols, chat, th, src) in [
        (SYMBOLS_CRYPTO, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto"),
        (SYMBOLS_FOREX, CHAT_FOREX, THRESHOLD_FOREX, "forex"),
        (SYMBOLS_ACTIONS, CHAT_ACTIONS, THRESHOLD_ACTIONS, "actions")
    ]:
        for sym in symbols:
            try:
                df = fetch_crypto(sym) if src == "crypto" else fetch_alpha_vantage(sym, fx=(src=="forex"))
                latest, signals = analyze(df)
                msg = f"""📊 {sym} — ({TIMEFRAME})
⚡ Signal initial → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix : {latest['close']:.2f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""
                send_msg(chat, msg)
                key = f"{chat}_{sym}"
                last_signals[key] = signals
                last_prices[key] = latest["close"]
            except Exception as e:
                print(f"Init erreur {sym}: {e}")

    while True:
        for sym in SYMBOLS_CRYPTO:
            send_signal(sym, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto")
        for sym in SYMBOLS_FOREX:
            send_signal(sym, CHAT_FOREX, THRESHOLD_FOREX, "forex")
        for sym in SYMBOLS_ACTIONS:
            send_signal(sym, CHAT_ACTIONS, THRESHOLD_ACTIONS, "actions")

        time.sleep(INTERVAL)

# =============================
# 💡 FLASK KEEPALIVE
# =============================
if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()

    app = Flask(__name__)
    @app.route('/')
    def home():
        return "Bot actif ✅"

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
