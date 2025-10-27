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
import yfinance as yf

# 🔑 TOKEN & CANAUX TELEGRAM
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"

# --- CRYPTO (Kraken) ---
CHAT_CRYPTO = "@TradeSignalAI"
SYMBOLS_CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
THRESHOLD_CRYPTO = 0.015  # ±1.5 %

# --- FOREX (Kraken) ---
CHAT_FOREX = "@TradeForexIA"
SYMBOLS_FOREX = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF"]
THRESHOLD_FOREX = 0.004  # ±0.4 %

# --- ACTIONS (Alpha Vantage / Yahoo) ---
CHAT_ACTIONS = "@TradeStocksAI"
SYMBOLS_ACTIONS = ["AAPL", "TSLA", "NVDA"]
THRESHOLD_ACTIONS = 0.006  # ±0.6 %

# 🔧 PARAMÈTRES TECHNIQUES
TIMEFRAME = "4h"
LIMIT = 150
INTERVAL = 300  # vérif toutes les 5 min

exchange = ccxt.kraken()

# Mémoire: dernier signal & dernier prix de référence
last_signals = {}
last_prices = {}

# -------------------------------
# 📤 ENVOI MESSAGE TELEGRAM
# -------------------------------
def send_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Erreur Telegram :", e)

# -------------------------------
# 🔹 INDICATEURS TECHNIQUES
# -------------------------------
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

# -------------------------------
# 🔍 ANALYSE TECHNIQUE
# -------------------------------
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

    # EMA
    if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
        signals.append("EMA — BUY")
    if ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
        signals.append("EMA — SELL")

    # RSI
    if rsi_v.iloc[-2] < 30 and rsi_v.iloc[-1] >= 30:
        signals.append("RSI — BUY")
    if rsi_v.iloc[-2] > 72 and rsi_v.iloc[-1] < 68:
        signals.append("RSI — SELL")

    # MACD
    if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append("MACD — BUY")
    if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append("MACD — SELL")

    # DOUBLE
    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

# -------------------------------
# 🔁 Vérification et envoi
# -------------------------------
def check_and_send(sym, chat_id, threshold, source="crypto"):
    try:
        if source == "crypto" or source == "forex":
            ohlcv = exchange.fetch_ohlcv(sym, TIMEFRAME, limit=LIMIT)
            df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
        else:
            df = yf.download(sym, period="60d", interval="1h", progress=False)
            df = df.resample("4H").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
            df.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"}, inplace=True)
            df.reset_index(inplace=True)

        latest, signals = analyze(df)
        current_price = latest["close"]
        key = f"{chat_id}_{sym}"
        last_signal = last_signals.get(key)
        last_price = last_prices.get(key)

        send = False
        update_prefix = ""

        # Premier signal (initial)
        if last_signal is None:
            send = True
            update_prefix = "🟢 Signal initial détecté\n"
            last_prices[key] = current_price

        # Nouveau signal
        elif signals and signals != last_signal:
            send = True
            last_prices[key] = current_price

        # Variation de prix
        elif last_price:
            change = (current_price - last_price) / last_price
            if abs(change) >= threshold:
                pct = change * 100.0
                if pct >= 0:
                    update_prefix = f"✅ {sym} — Le prix a augmenté de +{pct:.2f}% depuis le dernier signal.\n"
                else:
                    update_prefix = f"⚠️ {sym} — Le prix a baissé de {pct:.2f}% depuis le dernier signal.\n"
                send = True
                last_prices[key] = current_price

        if send and signals:
            msg = f"""
{update_prefix}📊 {sym} — ({TIMEFRAME})

📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20'] > latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.2f}) {'>' if latest['macd'] > latest['signal'] else '<'} Signal ({latest['signal']:.2f})
💪 RSI : {latest['rsi']:.1f}

⚡ Signal Global → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix : {latest['close']:.2f}
🕒 {datetime.now(pytz.timezone('Europe/Paris')).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(chat_id, msg)
            print(msg)
            last_signals[key] = signals

    except Exception as e:
        print(f"Erreur {sym}: {e}")

# -------------------------------
# 🔄 BOUCLE PRINCIPALE
# -------------------------------
def loop():
    print("🚀 Bot actif : en attente de signaux...")
    while True:
        for sym in SYMBOLS_CRYPTO:
            check_and_send(sym, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto")
        for sym in SYMBOLS_FOREX:
            check_and_send(sym, CHAT_FOREX, THRESHOLD_FOREX, "forex")
        for sym in SYMBOLS_ACTIONS:
            check_and_send(sym, CHAT_ACTIONS, THRESHOLD_ACTIONS, "actions")
        time.sleep(INTERVAL)

# -------------------------------
# 🚀 LANCEMENT (Render Ready)
# -------------------------------
if __name__ == "__main__":
    bot_thread = threading.Thread(target=loop, daemon=True)
    bot_thread.start()

    app = Flask(__name__)

    @app.route('/')
    def home():
        return "Bot actif ✅"

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
