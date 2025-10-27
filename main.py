import os, time, requests, threading
from datetime import datetime
import pytz
import pandas as pd
import numpy as np
import ccxt
from flask import Flask

# =========================
# 🔧 CONFIG
# =========================
TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"

# Canaux Telegram
CHAT_CRYPTO = "@TradeSignalAI"
CHAT_FOREX  = "@TradeForexIA"
CHAT_STOCKS = "@TradeStocksAI"

# Symboles
SYMBOLS_CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]  # Kraken
# On met plusieurs paires Forex. Si Kraken n’en liste pas une, elle sera juste loguée en erreur et on continue.
SYMBOLS_FOREX  = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF"]  # Kraken
SYMBOLS_STOCKS = ["NVDA", "MSFT", "AAPL"]  # Alpha Vantage

# Seuils de variation (après un signal)
THRESHOLD_CRYPTO = 0.015  # 1.5%
THRESHOLD_FOREX  = 0.004  # 0.4%
THRESHOLD_STOCKS = 0.006  # 0.6%

# Cadence
TIMEFRAME = "4h"           # pour Kraken
LIMIT     = 150
INTERVAL  = 300            # 5 minutes
TZ = pytz.timezone("Europe/Paris")

ALPHA_KEY = os.getenv("ALPHA_VANTAGE_KEY", "").strip()

# =========================
# 📤 Telegram
# =========================
def send_msg(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception as e:
        print("[Telegram]", e, flush=True)

# =========================
# 📐 Indicateurs
# =========================
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, period=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    dn = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / dn
    return 100 - (100 / (1 + rs))

def macd(s, fast=12, slow=26, signal=9):
    f = ema(s, fast); sl = ema(s, slow)
    line = f - sl
    sig  = ema(line, signal)
    return line, sig, line - sig

def analyze(df: pd.DataFrame):
    close = df["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    r     = rsi(close, 21)
    m, sig, _ = macd(close)

    latest = {
        "close": float(close.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "rsi":   float(r.iloc[-1]),
        "macd":  float(m.iloc[-1]),
        "signal":float(sig.iloc[-1]),
    }

    signals = []
    # EMA cross
    if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
        signals.append("EMA — BUY")
    if ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
        signals.append("EMA — SELL")

    # RSI (retour de zone)
    if r.iloc[-2] < 30 and r.iloc[-1] >= 30:
        signals.append("RSI — BUY")
    if r.iloc[-2] > 72 and r.iloc[-1] < 68:
        signals.append("RSI — SELL")

    # MACD cross
    if m.iloc[-2] <= sig.iloc[-2] and m.iloc[-1] > sig.iloc[-1]:
        signals.append("MACD — BUY")
    if m.iloc[-2] >= sig.iloc[-2] and m.iloc[-1] < sig.iloc[-1]:
        signals.append("MACD — SELL")

    # DOUBLE direction
    ema_dir  = "BUY" if latest["ema20"] > latest["ema50"] else "SELL"
    macd_dir = "BUY" if latest["macd"] > latest["signal"] else "SELL"
    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals

def format_msg(sym, latest, signals, prefix=""):
    return f"""{prefix}📊 {sym} — ({TIMEFRAME})

📈 EMA20 ({latest['ema20']:.2f}) {'>' if latest['ema20']>latest['ema50'] else '<'} EMA50 ({latest['ema50']:.2f})
📉 MACD ({latest['macd']:.4f}) {'>' if latest['macd']>latest['signal'] else '<'} Signal ({latest['signal']:.4f})
💪 RSI : {latest['rsi']:.1f}

⚡ Signal Global → {' / '.join(signals) if signals else 'Aucun signal'}
💰 Prix : {latest['close']:.5f}
🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
"""

# =========================
# 📡 Données Marché
# =========================
kraken = ccxt.kraken()

def fetch_ccxt_4h(sym: str) -> pd.DataFrame:
    o = kraken.fetch_ohlcv(sym, TIMEFRAME, limit=LIMIT)
    df = pd.DataFrame(o, columns=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df

def alpha_intraday_5m(symbol: str) -> pd.DataFrame:
    # Actions via Alpha Vantage, 5 min → resample en 4h
    if not ALPHA_KEY:
        raise Exception("ALPHA_VANTAGE_KEY manquante")
    url = ("https://www.alphavantage.co/query"
           f"?function=TIME_SERIES_INTRADAY&symbol={symbol}"
           f"&interval=5min&outputsize=compact&apikey={ALPHA_KEY}")
    r = requests.get(url, timeout=20)
    j = r.json()
    key = next((k for k in j.keys() if "Time Series" in k), None)
    if not key:  # rate limit / erreur
        raise Exception(f"AlphaVantage error for {symbol}: {j}")
    ts = j[key]
    rows = []
    for t, v in ts.items():
        rows.append({
            "time": pd.to_datetime(t),
            "open": float(v["1. open"]),
            "high": float(v["2. high"]),
            "low":  float(v["3. low"]),
            "close":float(v["4. close"]),
            "volume": float(v.get("5. volume", 0))
        })
    df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    df = (df.set_index("time")
            .resample("4H")
            .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
            .dropna()
            .reset_index())
    return df

# =========================
# 🧠 Anti-spam & variations
# =========================
last_signals = {}  # key = f"{chat}:{sym}" -> list[str]
last_prices  = {}  # key -> float

def check_and_send(sym: str, chat: str, threshold: float, source: str):
    try:
        if source in ("crypto", "forex"):
            df = fetch_ccxt_4h(sym)
        else:
            df = alpha_intraday_5m(sym)

        if df is None or df.empty:
            print(f"[Data vide] {source} {sym}", flush=True)
            return

        latest, signals = analyze(df)
        price = latest["close"]
        key = f"{chat}:{sym}"
        prev_sig = last_signals.get(key)
        base_px  = last_prices.get(key)

        send = False
        prefix = ""

        # 1) Premier passage = signal initial
        if prev_sig is None and signals:
            send = True
            prefix = "🟢 Signal initial détecté\n\n"
            last_prices[key] = price

        # 2) Nouveau signal
        elif signals and signals != prev_sig:
            send = True
            prefix = "📈 Nouveau signal détecté\n\n"
            last_prices[key] = price

        # 3) Variation de prix par rapport au dernier signal
        elif base_px is not None and prev_sig:
            chg = (price - base_px) / base_px
            if abs(chg) >= threshold:
                pct = chg*100
                prefix = (f"✅ {sym} — Prix +{pct:.2f}% depuis le dernier signal.\n\n"
                          if pct >= 0 else
                          f"⚠️ {sym} — Prix {pct:.2f}% depuis le dernier signal.\n\n")
                send = True
                last_prices[key] = price

        if send and signals:
            msg = format_msg(sym, latest, signals, prefix=prefix)
            send_msg(chat, msg)
            print(msg, flush=True)
            last_signals[key] = signals

    except Exception as e:
        print(f"[Erreur] {source} {sym}: {e}", flush=True)

# =========================
# 🔁 Boucle principale
# =========================
def loop():
    print("🚀 Bot actif : en attente de signaux...", flush=True)

    # Envoi des signaux initiaux
    for s in SYMBOLS_CRYPTO:
        try: check_and_send(s, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto")
        except Exception as e: print("[Init crypto]", s, e, flush=True)

    for s in SYMBOLS_FOREX:
        try: check_and_send(s, CHAT_FOREX, THRESHOLD_FOREX, "forex")
        except Exception as e: print("[Init forex]", s, e, flush=True)

    for s in SYMBOLS_STOCKS:
        try: check_and_send(s, CHAT_STOCKS, THRESHOLD_STOCKS, "stocks")
        except Exception as e: print("[Init stocks]", s, e, flush=True)

    # Boucle continue
    while True:
        for s in SYMBOLS_CRYPTO:
            check_and_send(s, CHAT_CRYPTO, THRESHOLD_CRYPTO, "crypto")
        for s in SYMBOLS_FOREX:
            check_and_send(s, CHAT_FOREX, THRESHOLD_FOREX, "forex")
        for s in SYMBOLS_STOCKS:
            check_and_send(s, CHAT_STOCKS, THRESHOLD_STOCKS, "stocks")
        time.sleep(INTERVAL)

# =========================
# 🚀 Lancement + Keepalive
# =========================
if __name__ == "__main__":
    t = threading.Thread(target=loop, daemon=True)
    t.start()

    app = Flask(__name__)
    @app.route("/")
    def home(): return "Bot actif ✅"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
