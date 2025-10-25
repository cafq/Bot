import ccxt
import pandas as pd
import numpy as np
import requests
import time

# 🔑 Paramètres
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"
CHAT_ID = "@TradesignalAI"
SYMBOLS = ["BTC/USDT", "SOL/USDT", "ETH/USDT"]
TIMEFRAME = "4h"
LIMIT = 150
INTERVAL = 300  # secondes entre chaque vérif (5 min)

exchange = ccxt.kraken()

def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Erreur Telegram :", e)

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

def analyze(df):
    close = df["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi_v = rsi(close, 21)
    macd_line, signal_line, hist = macd(close)

    latest = {
        "close": close.iloc[-1],
        "ema20": ema20.iloc[-1],
        "ema50": ema50.iloc[-1],
        "rsi": rsi_v.iloc[-1],
        "macd": macd_line.iloc[-1],
        "signal": signal_line.iloc[-1],
    }

    signals = []
    
    # Déterminer les directions EMA et MACD
    ema_dir = "BUY" if latest["ema20"] > latest["ema50"] else "SELL"
    macd_dir = "BUY" if latest["macd"] > latest["signal"] else "SELL"
    
    # EMA
    if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
        signals.append("EMA — BUY")
    if ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
        signals.append("EMA — SELL")

    # RSI avec nouvelles conditions strictes
    rsi_buy_signal = rsi_v.iloc[-2] < 30 and rsi_v.iloc[-1] >= 30
    rsi_sell_signal = rsi_v.iloc[-2] > 72 and rsi_v.iloc[-1] < 68
    
    # N'ajouter RSI BUY que si EMA et MACD ne sont pas en SELL
    if rsi_buy_signal and not (ema_dir == "SELL" and macd_dir == "SELL"):
        signals.append("RSI — BUY")
    
    # N'ajouter RSI SELL que si EMA et MACD ne sont pas en BUY
    if rsi_sell_signal and not (ema_dir == "BUY" and macd_dir == "BUY"):
        signals.append("RSI — SELL")

    # MACD
    if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append("MACD — BUY")
    if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append("MACD — SELL")

    # DOUBLE SIGNAL EMA + MACD
    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

def loop():
    while True:
        for sym in SYMBOLS:
            try:
                ohlcv = exchange.fetch_ohlcv(sym, TIMEFRAME, limit=LIMIT)
                df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
                df["time"] = pd.to_datetime(df["time"], unit="ms")
                latest, signals = analyze(df)
                if signals:
                   msg = f"""
📊 {sym} — ({TIMEFRAME})

📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20'] > latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.2f}) {'>' if latest['macd'] > latest['signal'] else '<'} Signal ({latest['signal']:.2f})
💪 RSI : {latest['rsi']:.1f}

⚡ Signal Global → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix : {latest['close']:.2f}
🕒 {time.strftime('%Y-%m-%d %H:%M:%S')}
"""

                   send_msg(msg)
                   print(msg)
            except Exception as e:
                print(f"Erreur {sym}: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    # Lance la boucle principale du bot
    import threading

    bot_thread = threading.Thread(target=loop)
    bot_thread.start()

    # --- Garde le Web Service Render actif ---
    import os
    from flask import Flask
    app = Flask(__name__)

    @app.route('/')
    def home():
        return "Bot actif ✅"

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
