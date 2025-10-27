import ccxt
import fxcmpy
import alpaca_trade_api as tradeapi
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
import pytz
import os
from flask import Flask
import threading

# 🔑 Telegram
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"
CHAT_CRYPTO = "@TradeSignalAI"
CHAT_FOREX = "@TradeForexIA"
CHAT_ACTIONS = "@TradeStocksAI"

# 📊 Symboles et seuils
SYMBOLS_CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SYMBOLS_FOREX = ["EUR/USD", "GBP/USD", "USD/JPY"]
SYMBOLS_ACTIONS = ["AAPL", "TSLA", "NVDA"]

THRESHOLD_CRYPTO = 0.017
THRESHOLD_FOREX = 0.003
THRESHOLD_ACTIONS = 0.008

TIMEFRAME = "4h"
LIMIT = 150
INTERVAL = 300  # 5 min

# Connexions API
exchange = ccxt.kraken()

fxcm = fxcmpy.fxcmpy(access_token="demo", log_level="error", server="demo")
api_alpaca = tradeapi.REST()

# Mémoire
last_signals = {}
last_prices = {}

# -------------------------------
# Fonctions techniques
# -------------------------------
def send_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    requests.post(url, data=data)

def ema(series, n): return series.ewm(span=n, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast, ema_slow = ema(series, fast), ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line

# -------------------------------
# Analyse
# -------------------------------
def analyze(df):
    close = df["close"]
    ema20, ema50 = ema(close, 20), ema(close, 50)
    rsi_v = rsi(close, 21)
    macd_line, signal_line, _ = macd(close)

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

    if rsi_v.iloc[-2] < 30 and rsi_v.iloc[-1] >= 30:
        signals.append("RSI — BUY")
    if rsi_v.iloc[-2] > 72 and rsi_v.iloc[-1] < 68:
        signals.append("RSI — SELL")

    if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append("MACD — BUY")
    if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append("MACD — SELL")

    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

# -------------------------------
# Données marché
# -------------------------------
def get_data(symbol, source="crypto"):
    if source == "crypto":
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LIMIT)
        df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
    elif source == "forex":
        data = fxcm.get_candles(symbol.replace("/", ""), period='H4', number=150)
        return data.rename(columns={"bidopen":"open","bidhigh":"high","bidlow":"low","bidclose":"close"})
    elif source == "stocks":
        bars = api_alpaca.get_bars(symbol, "4Hour", limit=150).df
        bars = bars.reset_index().rename(columns={"open":"open","high":"high","low":"low","close":"close","volume":"volume"})
        return bars
    else:
        raise ValueError("source invalide")

# -------------------------------
# Vérif + message
# -------------------------------
def check_and_send(sym, chat_id, threshold, source):
    try:
        df = get_data(sym, source)
        latest, signals = analyze(df)
        current_price = latest["close"]
        key = f"{chat_id}_{sym}"

        last_signal = last_signals.get(key)
        last_price = last_prices.get(key)
        send = False
        update_prefix = ""

        if signals and signals != last_signal:
            send = True
            last_prices[key] = current_price

        elif last_price:
            change = (current_price - last_price) / last_price
            if abs(change) >= threshold and last_signal:
                send = True
                pct = change * 100
                update_prefix = (
                    f"✅ {sym} +{pct:.2f}% depuis le dernier signal\n"
                    if pct >= 0 else
                    f"⚠️ {sym} {pct:.2f}% depuis le dernier signal\n"
                )
                last_prices[key] = current_price

        if send and signals:
            msg = f"""{update_prefix}
📊 {sym} ({TIMEFRAME})
📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20']>latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.2f}) {'>' if latest['macd']>latest['signal'] else '<'} Signal ({latest['signal']:.2f})
💪 RSI: {latest['rsi']:.1f}
⚡ Signal Global → {' / '.join(signals)}
💰 Prix: {latest['close']:.2f}
🕒 {datetime.now(pytz.timezone('Europe/Paris')).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(chat_id, msg)
            print(msg)
            last_signals[key] = signals
    except Exception as e:
        print(f"Erreur {sym}: {e}")

# -------------------------------
# Boucle principale
# -------------------------------
def loop():
    while True:
        for sym in SYMBOLS_CRYPTO:
            check_and_send(sym, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto")
        for sym in SYMBOLS_FOREX:
            check_and_send(sym, CHAT_FOREX, THRESHOLD_FOREX, "forex")
        for sym in SYMBOLS_ACTIONS:
            check_and_send(sym, CHAT_ACTIONS, THRESHOLD_ACTIONS, "stocks")
        time.sleep(INTERVAL)

# -------------------------------
# Lancement
# -------------------------------
if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app = Flask(__name__)

    @app.route('/')
    def home():
        return "Bot actif ✅"

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
