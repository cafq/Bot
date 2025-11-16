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

# ========= CONFIG GÉNÉRALE =========

TELEGRAM_TOKEN = "7381197277:AAFyOkwfQvqCRMnTiWYT-5eIr_tF6_lQbEU"  # ⬅️ METS TON TOKEN ICI

# Canaux Telegram
CHAT_SWING = "@TradeSignalAI"     # bot 4h (crypto + forex)
CHAT_SCALP = "@ScalpSignalAI"     # bot 15m (crypto only, crée un canal et mets son @ ici)

# Paires CRYPTO sur Kraken
SYMBOLS_CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# Paires FOREX sur Kraken (format ccxt/kraken : EUR/USD, etc.)
SYMBOLS_FOREX = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CAD"]

# Pour le code, on fait une liste globale pour le swing (4h)
SYMBOLS_SWING = SYMBOLS_CRYPTO + SYMBOLS_FOREX

# Timeframes
TF_SWING = "4h"
TF_SCALP = "15m"

# Intervalle entre deux vérifications (en secondes)
INTERVAL_SWING = 300   # 5 min pour le bot 4h
INTERVAL_SCALP = 120   # 2 min pour le scalp

# Seuil de variation de prix
PRICE_MOVE_SWING = 0.01    # 1% pour le bot 4h
PRICE_MOVE_SCALP = 0.0015  # 0.15% pour le scalp

# Exchange ccxt
exchange = ccxt.kraken()

# Mémoire des derniers signaux et prix par (timeframe, symbol)
last_signals = {}       # ex: {("4h","BTC/USDT"): ["EMA — BUY", "MACD — BUY", "DOUBLE BUY"]}
last_prices = {}        # ex: {("4h","BTC/USDT"): 41250.0}
last_summary_time = {}  # pour les résumés scalp 15m : {("15m","BTC/USDT"): timestamp}


# ========= OUTILS =========

def send_msg(chat_id: str, text: str):
    """Envoie un message dans un canal ou chat Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print("Erreur Telegram :", e)


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 150) -> pd.DataFrame:
    """Récupère les données OHLCV depuis Kraken et renvoie un DataFrame propre."""
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(
        ohlcv,
        columns=["time", "open", "high", "low", "close", "volume"]
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df


def analyze(df: pd.DataFrame):
    """
    Calcule EMA20, EMA50, MACD, RSI et renvoie :
    - latest : dict avec les dernières valeurs
    - signals : liste de signaux [ "EMA — BUY", "MACD — SELL", "DOUBLE BUY", ... ]
    """
    close = df["close"]
    volume = df["volume"]

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi_v = rsi(close, 21)
    macd_line, signal_line, hist = macd(close)

    latest = {
        "time": df["time"].iloc[-1],
        "close": float(close.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "rsi": float(rsi_v.iloc[-1]),
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
        "volume": float(volume.iloc[-1]),
    }

    signals = []

    # Direction EMA & MACD
    ema_dir = "BUY" if latest["ema20"] > latest["ema50"] else "SELL"
    macd_dir = "BUY" if latest["macd"] > latest["signal"] else "SELL"

    # Croisements EMA
    if len(ema20) >= 2 and len(ema50) >= 2:
        if ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1]:
            signals.append("EMA — BUY")
        elif ema20.iloc[-2] >= ema50.iloc[-2] and ema20.iloc[-1] < ema50.iloc[-1]:
            signals.append("EMA — SELL")

    # Croisements MACD
    if len(macd_line) >= 2 and len(signal_line) >= 2:
        if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
            signals.append("MACD — BUY")
        elif macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
            signals.append("MACD — SELL")

    # DOUBLE SIGNAL EMA + MACD (direction actuelle)
    if ema_dir == macd_dir:
        signals.append(f"DOUBLE {ema_dir}")

    return latest, signals


def build_message(symbol: str, timeframe: str, latest: dict, signals, prefix: str = "") -> str:
    """Construit le message Telegram avec toutes les infos techniques."""
    paris_tz = pytz.timezone("Europe/Paris")
    now_str = datetime.now(paris_tz).strftime("%Y-%m-%d %H:%M:%S")

    ema_relation = ">" if latest["ema20"] > latest["ema50"] else "<"
    macd_relation = ">" if latest["macd"] > latest["signal"] else "<"

    signals_text = " / ".join(signals) if signals else "Aucun signal"

    msg = f"""{prefix}📊 {symbol} — ({timeframe})

📈 EMA20 ({latest['ema20']:.5f}) {ema_relation} EMA50 ({latest['ema50']:.5f})
📉 MACD ({latest['macd']:.6f}) {macd_relation} Signal ({latest['signal']:.6f})
💪 RSI : {latest['rsi']:.3f}
📊 Volume : {latest['volume']:.2f}

⚡ Signal Global → {signals_text}
💰 Prix : {latest['close']:.5f}
🕒 {now_str}
"""
    return msg


# ========= LOGIQUE BOT 4H (SWING CRYPTO + FOREX) =========

def process_symbol_swing(symbol: str):
    """Traite un symbole pour le bot 4h en mode événement (crypto + forex)."""
    global last_signals, last_prices

    key = (TF_SWING, symbol)

    try:
        df = fetch_ohlcv(symbol, TF_SWING, limit=150)
        latest, signals = analyze(df)
        current_price = latest["close"]

        prev_signals = last_signals.get(key)
        prev_price = last_prices.get(key)

        send = False
        prefix = ""

        # 1) Nouveau signal (EMA / MACD / DOUBLE) ?
        if signals and signals != prev_signals:
            send = True
            prefix = "⚡ Nouveau signal détecté\n\n"
            last_signals[key] = signals
            last_prices[key] = current_price

        # 2) Variation de prix >= 1% par rapport au dernier prix mémorisé
        elif prev_price is not None and prev_signals:
            change = (current_price - prev_price) / prev_price
            if abs(change) >= PRICE_MOVE_SWING:
                direction = "augmenté" if change > 0 else "baissé"
                pct = change * 100
                prefix = f"📈 Le prix de {symbol} a {direction} de {pct:.2f}% depuis le dernier signal.\n\n"
                send = True
                last_prices[key] = current_price  # nouveau point de référence

        if send and signals:
            msg = build_message(symbol, TF_SWING, latest, signals, prefix=prefix)
            send_msg(CHAT_SWING, msg)
            print(msg)

    except Exception as e:
        print(f"[SWING] Erreur sur {symbol} :", e)


def loop_swing():
    """Boucle infinie pour le bot 4h (crypto + forex)."""
    while True:
        for sym in SYMBOLS_SWING:
            process_symbol_swing(sym)
        time.sleep(INTERVAL_SWING)


# ========= LOGIQUE BOT SCALP 15M (CRYPTO SEULEMENT) =========

def process_symbol_scalp(symbol: str):
    """Traite un symbole pour le bot scalp 15m (mode C : événements + résumé)."""
    global last_signals, last_prices, last_summary_time

    key = (TF_SCALP, symbol)

    try:
        df = fetch_ohlcv(symbol, TF_SCALP, limit=200)
        latest, signals = analyze(df)
        current_price = latest["close"]
        last_candle_time = latest["time"]  # timestamp de la dernière bougie 15m

        prev_signals = last_signals.get(key)
        prev_price = last_prices.get(key)
        prev_summary_time = last_summary_time.get(key)

        send_event = False
        event_prefix = ""

        # 1) Nouveau signal (EMA / MACD / DOUBLE) ?
        if signals and signals != prev_signals:
            send_event = True
            event_prefix = "⚡ Nouveau signal SCALP détecté\n\n"
            last_signals[key] = signals
            last_prices[key] = current_price

        # 2) Variation de prix >= seuil scalp par rapport au dernier prix
        elif prev_price is not None and prev_signals:
            change = (current_price - prev_price) / prev_price
            if abs(change) >= PRICE_MOVE_SCALP:
                direction = "augmenté" if change > 0 else "baissé"
                pct = change * 100
                event_prefix = f"📈 (SCALP) Le prix de {symbol} a {direction} de {pct:.2f}% depuis le dernier signal.\n\n"
                send_event = True
                last_prices[key] = current_price

        # Envoi des événements
        if send_event and signals:
            msg = build_message(symbol, TF_SCALP, latest, signals, prefix=event_prefix)
            send_msg(CHAT_SCALP, msg)
            print(msg)

        # 3) Résumé à chaque nouvelle bougie 15m (Option C)
        if prev_summary_time is None or last_candle_time > prev_summary_time:
            summary_prefix = "🕒 Résumé SCALP (nouvelle bougie 15m)\n\n"
            summary_msg = build_message(symbol, TF_SCALP, latest, signals, prefix=summary_prefix)
            send_msg(CHAT_SCALP, summary_msg)
            print(summary_msg)
            last_summary_time[key] = last_candle_time

    except Exception as e:
        print(f"[SCALP] Erreur sur {symbol} :", e)


def loop_scalp():
    """Boucle infinie pour le bot scalp 15m (CRYPTO seulement)."""
    while True:
        for sym in SYMBOLS_CRYPTO:
            process_symbol_scalp(sym)
        time.sleep(INTERVAL_SCALP)


# ========= LANCEMENT (Render / Flask) =========

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot 4h (crypto + forex) + Scalp 15m (crypto) actif ✅"


if __name__ == "__main__":
    # Threads pour les deux bots
    swing_thread = threading.Thread(target=loop_swing, daemon=True)
    scalp_thread = threading.Thread(target=loop_scalp, daemon=True)

    swing_thread.start()
    scalp_thread.start()

    # Flask pour que Render garde le service en vie
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
