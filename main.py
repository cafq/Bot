import ccxt
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
import pytz
import threading
from flask import Flask
import os

# 🔑 Token & canaux Telegram
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"
CHAT_CRYPTO = "@TradeSignalAI"
CHAT_FOREX = "@TradeForexIA"

# ⚙️ Configuration
SYMBOLS_CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SYMBOLS_FOREX = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CAD"]

TIMEFRAME = "4h"
LIMIT = 150
INTERVAL = 300  # vérif toutes les 5 min
exchange = ccxt.kraken()

# 🧠 Mémoire pour signaux
last_signals = {}
last_prices = {}

# ✉️ Envoi Telegram
def send_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Erreur Telegram :", e)

# 📈 Calculs techniques
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
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# 🔍 Analyse technique
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

    if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
        signals.append("EMA — BUY")
    if ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
        signals.append("EMA — SELL")

    if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append("MACD — BUY")
    if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append("MACD — SELL")

    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

# 🔁 Vérification et envoi
def check_and_send(sym, chat_id):
    try:
        ohlcv = exchange.fetch_ohlcv(sym, TIMEFRAME, limit=LIMIT)
        df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")

        latest, signals = analyze(df)
        current_price = latest["close"]
        key = f"{chat_id}_{sym}"
        last_signal = last_signals.get(key)
        last_price = last_prices.get(key, current_price)
        pct_change = ((current_price - last_price) / last_price) * 100

        prefix = ""
        send = False

        if not last_signal:
            prefix = "🚀 Signal initial détecté\n"
            send = True
        elif signals != last_signal:
            prefix = "⚠️ Nouveau signal détecté\n"
            send = True
        else:
            prefix = "📊 Suivi de tendance (toutes les 5 min)\n"
            send = True  # toujours envoyer suivi

        if send:
            msg = f"""{prefix}
💱 {sym} — (4h)

📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20'] > latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.2f}) {'>' if latest['macd'] > latest['signal'] else '<'} Signal ({latest['signal']:.2f})
💪 RSI : {latest['rsi']:.1f}
💰 Prix : {current_price:.2f} ({pct_change:+.2f}%)
⚡ Signal Global → {' / '.join(signals) if signals else 'Aucun signal'}
🕒 {datetime.now(pytz.timezone('Europe/Paris')).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(chat_id, msg)
            print(msg)
            last_signals[key] = signals
            last_prices[key] = current_price

    except Exception as e:
        print(f"Erreur {sym}: {e}")

# 🔄 Boucle principale
def loop():
    while True:
        for sym in SYMBOLS_CRYPTO:
            check_and_send(sym, CHAT_CRYPTO)
        for sym in SYMBOLS_FOREX:
            check_and_send(sym, CHAT_FOREX)
        time.sleep(INTERVAL)

# 🚀 Lancement
if __name__ == "__main__":
    bot_thread = threading.Thread(target=loop, daemon=True)
    bot_thread.start()

    app = Flask(__name__)
    @app.route('/')
    def home():
        return "Bot actif ✅"
import sys

def keep_alive():
    while True:
        print("💤 Bot monitor — thread check OK")
        sys.stdout.flush()
        global bot_thread 
        if not bot_thread.is_alive():
            print("⚠️ Thread mort — relance du bot")
            try: 
                bot_thread = threading.Thread(target=loop, daemon=True)
                bot_thread.start()
            except Exception as e:
                print("Erreur relance :", e)
        time.sleep(60)

monitor_thread = threading.Thread(target=keep_alive, daemon=True)
monitor_thread.start()

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
