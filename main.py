import ccxt
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
import pytz
import os
from flask import Flask
import threading
import math

# =============================
# 🔑 CONFIG
# =============================
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"

# Canaux Telegram
CHAT_CRYPTO  = "@TradeSignalAI"
CHAT_FOREX   = "@TradeForexIA"
CHAT_ACTIONS = "@TradeStocksAI"

# Actifs suivis
SYMBOLS_CRYPTO  = ["BTC/USDT", "SOL/USDT", "ETH/USDT"]  # Kraken (ccxt)
# Finnhub Forex utilise un fournisseur (ici OANDA: ...)
SYMBOLS_FOREX = ["FX:EURUSD", "FX:GBPUSD", "FX:USDJPY", "FOREXCOM:XAUUSD"]
# Finnhub stocks: tickers US directs
SYMBOLS_ACTIONS = ["AAPL", "TSLA", "NVDA", "SPY"]

# Seuils de variation (alertes prix)
THRESHOLD_CRYPTO  = 0.017  # ±1.7%
THRESHOLD_FOREX   = 0.003  # ±0.3%
THRESHOLD_ACTIONS = 0.008  # ±0.8%

# Cadence
INTERVAL  = 300  # 5 minutes
TIMEFRAME = "4h"
LIMIT     = 150
TZ = pytz.timezone("Europe/Paris")

# Clés API
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "d3vl1lpr01qnbogt4p40d3vl1lpr01qnbogt4p4g")

# =============================
# 📡 EXCHANGES
# =============================
exchange = ccxt.kraken()

# Mémoires anti-spam
last_signals = {}  # key: channel+symbol -> list[str]
last_prices  = {}  # key: channel+symbol -> float

# =============================
# 📤 TELEGRAM
# =============================
def send_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception as e:
        print(f"[Telegram] {e}", flush=True)

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
    ema_dir  = "BUY"  if latest["ema20"] > latest["ema50"] else "SELL"
    macd_dir = "BUY"  if latest["macd"] > latest["signal"] else "SELL"

    # EMA cross
    if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
        signals.append("EMA — BUY")
    if ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
        signals.append("EMA — SELL")

    # RSI extrêmes (retour zone)
    if rsi_v.iloc[-2] < 30 and rsi_v.iloc[-1] >= 30:
        signals.append("RSI — BUY")
    if rsi_v.iloc[-2] > 72 and rsi_v.iloc[-1] < 68:
        signals.append("RSI — SELL")

    # MACD cross
    if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append("MACD — BUY")
    if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append("MACD — SELL")

    # Double (confirmation EMA+MACD même sens)
    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

# =============================
# 📊 FETCH DATA (KRKN + FINNHUB)
# =============================
def fetch_crypto_ohlcv_4h(sym: str):
    ohlcv = exchange.fetch_ohlcv(sym, "4h", limit=LIMIT)
    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df

def _fh_time_range(days=90):
    # Retourne from/to en epoch secondes
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=days)
    return int(from_dt.timestamp()), int(to_dt.timestamp())

def fetch_fh_forex_4h(symbol: str):
    # symbol ex: "OANDA:EUR_USD"
    frm, to = _fh_time_range(120)
    url = f"https://finnhub.io/api/v1/forex/candle?symbol={symbol}&resolution=60&from={frm}&to={to}&token={FINNHUB_KEY}"
    r = requests.get(url, timeout=30)
    data = r.json()
    if data.get("s") != "ok":
        raise Exception(f"Finnhub FX error {symbol}: {data}")
    df = pd.DataFrame({
        "time": [datetime.utcfromtimestamp(ts) for ts in data["t"]],
        "open": data["o"],
        "high": data["h"],
        "low":  data["l"],
        "close":data["c"],
        "volume": data.get("v", [0]*len(data["t"]))
    })
    df = df.sort_values("time").reset_index(drop=True)
    df = df.set_index("time").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna().reset_index()
    return df.tail(LIMIT)

def fetch_fh_stock_4h(symbol: str):
    frm, to = _fh_time_range(240)
    url = f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}&resolution=60&from={frm}&to={to}&token={FINNHUB_KEY}"
    r = requests.get(url, timeout=30)
    data = r.json()
    if data.get("s") != "ok":
        raise Exception(f"Finnhub STOCK error {symbol}: {data}")
    df = pd.DataFrame({
        "time": [datetime.utcfromtimestamp(ts) for ts in data["t"]],
        "open": data["o"],
        "high": data["h"],
        "low":  data["l"],
        "close":data["c"],
        "volume": data.get("v", [0]*len(data["t"]))
    })
    df = df.sort_values("time").reset_index(drop=True)
    df = df.set_index("time").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna().reset_index()
    return df.tail(LIMIT)

# =============================
# 📩 FORMAT & ENVOI
# =============================
def format_signal_message(sym, latest, signals, prefix=""):
    return f"""{prefix}📊 {sym} — ({TIMEFRAME})

📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20'] > latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.2f}) {'>' if latest['macd'] > latest['signal'] else '<'} Signal ({latest['signal']:.2f})
💪 RSI : {latest['rsi']:.1f}

⚡ Signal Global → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix : {latest['close']:.2f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""

def check_and_send(sym, chat_id, threshold, source):
    try:
        if source == "crypto":
            df = fetch_crypto_ohlcv_4h(sym)
        elif source == "forex":
            df = fetch_fh_forex_4h(sym)
        else:  # "stock"
            df = fetch_fh_stock_4h(sym)

        if df is None or df.empty:
            print(f"[Data] vide pour {sym} ({source})", flush=True)
            return

        latest, signals = analyze(df)
        current_price = latest["close"]
        key = f"{chat_id}_{sym}"
        last_signal = last_signals.get(key)
        last_price  = last_prices.get(key)

        send = False
        prefix = ""

        # Nouveau signal OU premier passage
        if signals and (last_signal is None or signals != last_signal):
            send = True
            last_prices[key] = current_price

        # Variation de prix
        elif last_price is not None and last_signal:
            change = (current_price - last_price) / last_price
            if abs(change) >= threshold:
                pct = change * 100.0
                prefix = (f"✅ {sym} — Prix +{pct:.2f}% depuis le dernier signal.\n\n"
                          if pct >= 0 else
                          f"⚠️ {sym} — Prix {pct:.2f}% depuis le dernier signal.\n\n")
                send = True
                last_prices[key] = current_price

        if send and signals:
            msg = format_signal_message(sym, latest, signals, prefix=prefix)
            send_msg(chat_id, msg)
            print(msg, flush=True)
            last_signals[key] = signals

    except Exception as e:
        print(f"[Erreur] {sym} ({source}): {e}", flush=True)

# =============================
# 🚦 SIGNAL INITIAL
# =============================
def send_initial_signals():
    print("🔹 Envoi des signaux initiaux…", flush=True)

    # Crypto
    for sym in SYMBOLS_CRYPTO:
        try:
            df = fetch_crypto_ohlcv_4h(sym)
            if df.empty: continue
            latest, signals = analyze(df)
            msg = f"""📊 {sym} — ({TIMEFRAME})
⚡ Signal initial → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix actuel : {latest['close']:.2f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(CHAT_CRYPTO, msg)
            print(msg, flush=True)
            key = f"{CHAT_CRYPTO}_{sym}"
            last_signals[key] = signals
            last_prices[key]  = latest["close"]
        except Exception as e:
            print(f"[Init] Crypto {sym}: {e}", flush=True)

    # Forex
    for sym in SYMBOLS_FOREX:
        try:
            df = fetch_fh_forex_4h(sym)
            if df.empty: continue
            latest, signals = analyze(df)
            msg = f"""📊 {sym} — ({TIMEFRAME})
⚡ Signal initial → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix actuel : {latest['close']:.5f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(CHAT_FOREX, msg)
            print(msg, flush=True)
            key = f"{CHAT_FOREX}_{sym}"
            last_signals[key] = signals
            last_prices[key]  = latest["close"]
        except Exception as e:
            print(f"[Init] Forex {sym}: {e}", flush=True)

    # Actions
    for sym in SYMBOLS_ACTIONS:
        try:
            df = fetch_fh_stock_4h(sym)
            if df.empty: continue
            latest, signals = analyze(df)
            msg = f"""📊 {sym} — ({TIMEFRAME})
⚡ Signal initial → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix actuel : {latest['close']:.2f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""
            send_msg(CHAT_ACTIONS, msg)
            print(msg, flush=True)
            key = f"{CHAT_ACTIONS}_{sym}"
            last_signals[key] = signals
            last_prices[key]  = latest["close"]
        except Exception as e:
            print(f"[Init] Stock {sym}: {e}", flush=True)

# =============================
# 🔁 BOUCLE PRINCIPALE
# =============================
def loop():
    send_initial_signals()
    while True:
        for sym in SYMBOLS_CRYPTO:
            check_and_send(sym, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto")
        for sym in SYMBOLS_FOREX:
            check_and_send(sym, CHAT_FOREX, THRESHOLD_FOREX, "forex")
        for sym in SYMBOLS_ACTIONS:
            check_and_send(sym, CHAT_ACTIONS, THRESHOLD_ACTIONS, "stock")
        time.sleep(INTERVAL)

# =============================
# 🚀 LANCEMENT + KEEPALIVE
# =============================
if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()

    app = Flask(__name__)
    @app.route("/")
    def home():
        return "Bot actif ✅"
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
