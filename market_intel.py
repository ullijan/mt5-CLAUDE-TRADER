# =============================================================================
# market_intel.py — Externe Markt-Signale als Bestätigung
# C:\mt5_agent\market_intel.py
#
# Holt kostenlose Analyse-Daten von:
#   1. Investing.com Technical Summary (Buy/Sell/Neutral pro TF)
#   2. Wirtschaftskalender (wichtige Events heute)
#
# Claude bekommt diese Daten als Zusatz-Kontext:
#   → Doppelte Bestätigung: Chart-Analyse + externe Signale
# =============================================================================

from __future__ import annotations
import requests
import re
import json
from datetime import datetime
from typing import Optional

from logger_setup import get_logger

log = get_logger("intel")

# Investing.com Symbol-IDs (manuell zugeordnet)
INVESTING_PAIRS = {
    "EURUSD": "1",    "GBPUSD": "2",    "USDJPY": "3",    "USDCHF": "4",
    "USDCAD": "7",    "AUDUSD": "5",    "NZDUSD": "8",    "EURGBP": "6",
    "EURJPY": "9",    "GBPJPY": "10",   "EURAUD": "15",   "EURCAD": "16",
    "EURCHF": "11",   "EURNZD": "52",   "GBPAUD": "26",   "GBPCAD": "40",
    "GBPCHF": "13",   "GBPNZD": "53",   "AUDCAD": "47",   "AUDCHF": "49",
    "AUDJPY": "48",   "AUDNZD": "50",   "CADJPY": "51",   "CHFJPY": "12",
    "NZDCAD": "54",   "NZDJPY": "55",   "XAUUSD": "68",   "XAGUSD": "69",
    "XTIUSD": "8849",  # WTI Öl
}

# Cache um nicht zu oft zu fetchen
_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 900   # 15 Minuten


def get_technical_summary(symbol: str) -> Optional[str]:
    """
    Holt Technical Summary für ein Symbol.
    Gibt lesbaren Text zurück: "Daily: Strong Sell | H4: Sell | H1: Neutral"
    """
    import time

    # Cache check
    cache_key = f"tech_{symbol}"
    if cache_key in _cache:
        cached, ts = _cache[cache_key]
        if time.time() - ts < CACHE_TTL:
            return cached

    pair_id = INVESTING_PAIRS.get(symbol)
    if not pair_id:
        return None

    try:
        # Investing.com Technical Summary API
        url = f"https://api.investing.com/api/financialdata/technical/analysis/{pair_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Domain-Id": "www",
        }

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            log.debug(f"Investing.com {symbol}: Status {resp.status_code}")
            return _fallback_summary(symbol)

        data = resp.json()

        # Parse response
        summaries = {}
        for period in data.get("technicalAnalysis", []):
            tf = period.get("period", "?")
            signal = period.get("signal", "neutral")
            summaries[tf] = signal.upper()

        if summaries:
            parts = []
            for tf in ["daily", "weekly", "monthly"]:
                if tf in summaries:
                    parts.append(f"{tf.capitalize()}: {summaries[tf]}")

            result = " | ".join(parts) if parts else "Keine Daten"
            _cache[cache_key] = (result, time.time())
            return result

    except Exception as e:
        log.debug(f"Investing.com {symbol}: {e}")

    return _fallback_summary(symbol)


def _fallback_summary(symbol: str) -> Optional[str]:
    """Fallback: Versuche über Web-Scraping die Summary zu holen."""
    try:
        # Einfacher Fallback über die technicals Seite
        pair_slug = symbol.lower()
        if symbol == "XAUUSD":
            pair_slug = "xau-usd"
        elif symbol == "XAGUSD":
            pair_slug = "xag-usd"
        else:
            pair_slug = f"{symbol[:3].lower()}-{symbol[3:].lower()}"

        url = f"https://www.investing.com/currencies/{pair_slug}-technical"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None

        text = resp.text

        # Suche nach Summary-Signalen im HTML
        signals = {}
        for tf, label in [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")]:
            # Vereinfachte Suche nach Buy/Sell/Neutral
            pattern = f'{label}.*?(Strong Buy|Buy|Neutral|Sell|Strong Sell)'
            match = re.search(pattern, text[:50000], re.IGNORECASE)
            if match:
                signals[label] = match.group(1).upper()

        if signals:
            parts = [f"{k}: {v}" for k, v in signals.items()]
            return " | ".join(parts)

    except Exception as e:
        log.debug(f"Fallback {symbol}: {e}")

    return None


def get_economic_events() -> str:
    """Holt wichtige Wirtschafts-Events für heute."""
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return "Wirtschaftskalender nicht verfügbar."

        events = resp.json()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # High-Impact Events heute
        important = []
        for event in events:
            date_str = event.get("date", "")
            if today in date_str and event.get("impact", "") == "High":
                important.append(
                    f"  {event.get('time', '?')} | {event.get('country', '?')} | "
                    f"{event.get('title', '?')} | Forecast: {event.get('forecast', '?')}"
                )

        if important:
            return "HIGH-IMPACT EVENTS HEUTE:\n" + "\n".join(important[:10])
        else:
            return "Keine High-Impact Events heute."

    except Exception as e:
        log.debug(f"Economic Calendar: {e}")
        return "Wirtschaftskalender nicht verfügbar."


def _calc_pivot_points(symbol: str) -> Optional[dict]:
    """Berechnet Classic + Fibonacci Pivot Points aus gestrigem D1-Candle."""
    try:
        import MetaTrader5 as mt5

        tf = mt5.TIMEFRAME_D1
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, 1)  # Gestrige Kerze
        if rates is None or len(rates) == 0:
            return None

        h = float(rates[0][2])   # High
        l = float(rates[0][3])   # Low
        c = float(rates[0][4])   # Close

        # Classic Pivot Points
        p = (h + l + c) / 3
        r1 = 2 * p - l
        r2 = p + (h - l)
        r3 = h + 2 * (p - l)
        s1 = 2 * p - h
        s2 = p - (h - l)
        s3 = l - 2 * (h - p)

        # Fibonacci Levels (38.2%, 61.8%, 100%)
        diff = h - l
        fib_r1 = p + 0.382 * diff
        fib_r2 = p + 0.618 * diff
        fib_r3 = p + diff
        fib_s1 = p - 0.382 * diff
        fib_s2 = p - 0.618 * diff
        fib_s3 = p - diff

        return {
            "pivot": p,
            "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3,
            "fib_r1": fib_r1, "fib_r2": fib_r2, "fib_r3": fib_r3,
            "fib_s1": fib_s1, "fib_s2": fib_s2, "fib_s3": fib_s3,
            "yesterday_high": h, "yesterday_low": l, "yesterday_close": c,
        }
    except Exception as e:
        log.debug(f"Pivot Points {symbol}: {e}")
        return None


def _format_price(symbol: str, price: float) -> str:
    """Formatiert Preis passend zum Symbol."""
    if "JPY" in symbol:
        return f"{price:.3f}"
    elif "XAU" in symbol:
        return f"{price:.2f}"
    elif "XAG" in symbol or "XTI" in symbol or "OIL" in symbol:
        return f"{price:.3f}"
    else:
        return f"{price:.5f}"


def get_market_intel(symbol: str) -> str:
    """
    Kompletter Market-Intelligence-Text für ein Symbol.
    Enthält: Technical Summary + Pivot Points + Key Levels.
    """
    lines = ["EXTERNE SIGNALE & LEVELS:"]

    # Technical Summary
    tech = get_technical_summary(symbol)
    if tech:
        lines.append(f"  Investing.com Signal: {tech}")

    # Pivot Points (konkrete Preislevels für SL/TP)
    pivots = _calc_pivot_points(symbol)
    if pivots:
        fp = lambda p: _format_price(symbol, p)
        lines.append(f"  PIVOT POINTS (gestern H:{fp(pivots['yesterday_high'])} L:{fp(pivots['yesterday_low'])} C:{fp(pivots['yesterday_close'])}):")
        lines.append(f"    R3: {fp(pivots['r3'])}  |  R2: {fp(pivots['r2'])}  |  R1: {fp(pivots['r1'])}")
        lines.append(f"    Pivot: {fp(pivots['pivot'])}")
        lines.append(f"    S1: {fp(pivots['s1'])}  |  S2: {fp(pivots['s2'])}  |  S3: {fp(pivots['s3'])}")
        lines.append(f"  FIBONACCI LEVELS:")
        lines.append(f"    FR3: {fp(pivots['fib_r3'])}  |  FR2: {fp(pivots['fib_r2'])}  |  FR1: {fp(pivots['fib_r1'])}")
        lines.append(f"    FS1: {fp(pivots['fib_s1'])}  |  FS2: {fp(pivots['fib_s2'])}  |  FS3: {fp(pivots['fib_s3'])}")
        lines.append(f"  → NUTZE diese Levels für SL/TP! BUY: SL nahe S1/S2, TP nahe R1/R2. SELL: SL nahe R1/R2, TP nahe S1/S2.")

    return "\n".join(lines)


def get_market_overview() -> str:
    """Überblick für den täglichen Scan — Events + allgemeine Stimmung."""
    lines = []

    # Economic Events
    events = get_economic_events()
    lines.append(events)

    return "\n".join(lines)
