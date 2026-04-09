# =============================================================================
# scanner.py — Top-Down Multi-Timeframe Scanner v6
# C:\mt5_agent\scanner.py
#
# Top-Down Workflow (wie ein echter Trader):
#   1. D1 Chart: Trend bestimmen, Key Levels identifizieren
#   2. H4 Chart: Ist Preis an einer D1-Zone? Struktur prüfen
#   3. H1 Chart: Entry-Signal suchen NUR wenn H4 passt
#
# Kein Trade ohne dass ALLE Timeframes geprüft wurden.
# Chart-Bilder werden für Claude Vision generiert.
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import datetime

from config import (
    SYMBOLS, BARS_TO_FETCH,
    MIN_SL_PIPS, MAX_SL_PIPS, MIN_RR_RATIO,
    _auto_pip_value, DEFAULT_MIN_SL, DEFAULT_MAX_SL,
    MIN_CONFLUENCE,
)
from logger_setup import get_logger

log = get_logger("scanner")

try:
    from smartmoneyconcepts import smc
    SMC_AVAILABLE = True
except ImportError:
    SMC_AVAILABLE = False
    log.warning("smartmoneyconcepts nicht installiert")

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1,
}


@dataclass
class Candle:
    time: int; open: float; high: float; low: float; close: float; volume: int
    @property
    def body(self): return abs(self.close - self.open)
    @property
    def range(self): return self.high - self.low
    @property
    def is_bullish(self): return self.close > self.open
    @property
    def is_bearish(self): return self.close < self.open
    @property
    def upper_wick(self): return self.high - max(self.open, self.close)
    @property
    def lower_wick(self): return min(self.open, self.close) - self.low


@dataclass
class Setup:
    symbol: str; timeframe: str; pattern: str; direction: str
    entry_price: float; sl_price: float; tp_price: float
    sl_pips: float; tp_pips: float; rr: float
    confluence: int; signals: list; context: str; htf_trend: str
    d1_bias: str          # D1 Trend-Bias
    h4_at_zone: bool      # Ist H4 an einer D1-Zone?
    chart_data: dict = field(default_factory=dict)   # TF → DataFrame für Chart-Rendering
    candles: list = field(default_factory=list)


# === Hilfsfunktionen ===

def pip_value(symbol: str) -> float:
    return _auto_pip_value(symbol)

def pips_to_price(symbol: str, pips: float) -> float:
    return pips * pip_value(symbol)

def price_to_pips(symbol: str, price_diff: float) -> float:
    pv = pip_value(symbol)
    return price_diff / pv if pv > 0 else 0.0

def fetch_candles(symbol: str, timeframe: str, count: int = BARS_TO_FETCH) -> list[Candle]:
    tf = TF_MAP.get(timeframe)
    if tf is None: return []
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0: return []
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5])) for r in rates]

def fetch_ohlcv_df(symbol: str, timeframe: str, count: int = BARS_TO_FETCH) -> Optional[pd.DataFrame]:
    """Holt OHLCV als DataFrame mit DatetimeIndex (für Charts + SMC)."""
    tf = TF_MAP.get(timeframe)
    if tf is None: return None
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0: return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    # Sicherstellen dass alle nötigen Spalten da sind
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            return None
    return df[["open", "high", "low", "close", "volume"]]

def _calc_atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1: return 0.0
    trs = [max(candles[-i].high - candles[-i].low, abs(candles[-i].high - candles[-i-1].close),
               abs(candles[-i].low - candles[-i-1].close)) for i in range(1, period + 1)]
    return float(np.mean(trs))

def _get_trend(candles: list[Candle], period: int = 20) -> str:
    if len(candles) < period + 5: return "NEUTRAL"
    closes = [c.close for c in candles[-period:]]
    ema_f, ema_s = np.mean(closes[-8:]), np.mean(closes)
    c = candles[-1].close
    if c > ema_f > ema_s: return "UP"
    if c < ema_f < ema_s: return "DOWN"
    return "NEUTRAL"

def _find_sr_levels(candles, lookback=50):
    if len(candles) < lookback: lookback = len(candles)
    recent = candles[-lookback:]
    sup, res = [], []
    for i in range(2, len(recent) - 2):
        if all(recent[i].low < recent[i+d].low for d in [-2,-1,1,2]): sup.append(recent[i].low)
        if all(recent[i].high > recent[i+d].high for d in [-2,-1,1,2]): res.append(recent[i].high)
    return sup, res

def _session_quality():
    h = datetime.datetime.utcnow().hour
    if 7 <= h <= 11: return "LONDON"
    if 12 <= h <= 16: return "NY_OVERLAP"
    if 17 <= h <= 21: return "NY"
    return "ASIA"

def _validate_sl_tp(symbol, sl_pips, tp_pips):
    min_sl = MIN_SL_PIPS.get(symbol, DEFAULT_MIN_SL)
    max_sl = MAX_SL_PIPS.get(symbol, DEFAULT_MAX_SL)
    if sl_pips < min_sl or sl_pips > max_sl: return False
    return (tp_pips / sl_pips if sl_pips > 0 else 0) >= MIN_RR_RATIO


# === Step 1: D1 Analyse (Bias + Key Levels) ===

def _analyze_d1(symbol: str) -> dict:
    """D1 Analyse: Trend-Bias und Major Support/Resistance."""
    candles = fetch_candles(symbol, "D1", 150)
    if len(candles) < 50:
        return {"bias": "NEUTRAL", "supports": [], "resistances": [], "atr": 0, "candles": candles}

    trend = _get_trend(candles, 50)   # Längere Periode für D1
    sups, ress = _find_sr_levels(candles, 100)
    atr = _calc_atr(candles, 14)

    return {
        "bias": trend,
        "supports": sups,
        "resistances": ress,
        "atr": atr,
        "candles": candles,
    }


# === Step 2: H4 Analyse (Zone-Check) ===

def _analyze_h4(symbol: str, d1_data: dict) -> dict:
    """H4 Analyse: Ist der Preis an einer D1-Zone? Struktur."""
    candles = fetch_candles(symbol, "H4", 100)
    if len(candles) < 30:
        return {"at_zone": False, "trend": "NEUTRAL", "zone_type": "", "candles": candles, "atr": 0}

    trend = _get_trend(candles, 20)
    atr = _calc_atr(candles, 14)
    price = candles[-1].close
    pip = pip_value(symbol)

    # Prüfe ob Preis nahe an einer D1 S/R Zone ist
    tolerance = d1_data["atr"] * 0.5 if d1_data["atr"] > 0 else atr * 0.5
    at_zone = False
    zone_type = ""

    for s in d1_data["supports"]:
        if abs(price - s) <= tolerance:
            at_zone = True
            zone_type = "D1_SUPPORT"
            break

    if not at_zone:
        for r in d1_data["resistances"]:
            if abs(price - r) <= tolerance:
                at_zone = True
                zone_type = "D1_RESISTANCE"
                break

    # SMC Check: FVG oder Order Block in der Nähe?
    smc_signals = []
    if SMC_AVAILABLE and len(candles) >= 20:
        df = pd.DataFrame({"open": [c.open for c in candles], "high": [c.high for c in candles],
                           "low": [c.low for c in candles], "close": [c.close for c in candles],
                           "volume": [c.volume for c in candles]})
        try:
            fvg_result = smc.fvg(df)
            for i in range(max(0, len(fvg_result) - 5), len(fvg_result)):
                r = fvg_result.iloc[i]
                if pd.notna(r.get("FVG", None)) and r["FVG"] != 0:
                    if not pd.notna(r.get("MitigatedIndex", None)):
                        smc_signals.append("FVG")
                        if not at_zone:
                            at_zone = True
                            zone_type = "H4_FVG"
        except: pass

        try:
            swing_hl = smc.swing_highs_lows(df, swing_length=5)
            ob_result = smc.ob(df, swing_hl)
            for i in range(max(0, len(ob_result) - 10), len(ob_result)):
                r = ob_result.iloc[i]
                if pd.notna(r.get("OB", None)) and r["OB"] != 0:
                    top, bot = r.get("Top", 0), r.get("Bottom", 0)
                    if bot <= price * 1.005 and top >= price * 0.995:
                        smc_signals.append("OrderBlock")
                        if not at_zone:
                            at_zone = True
                            zone_type = "H4_OB"
        except: pass

    return {
        "at_zone": at_zone,
        "zone_type": zone_type,
        "trend": trend,
        "atr": atr,
        "candles": candles,
        "smc_signals": smc_signals,
    }


# === Step 3: H1 Entry-Signal ===

def _find_h1_entries(symbol: str, d1_data: dict, h4_data: dict) -> list[dict]:
    """H1 Analyse: Entry-Signale suchen die mit D1+H4 übereinstimmen."""
    candles = fetch_candles(symbol, "H1", 80)
    if len(candles) < 20:
        return []

    atr = _calc_atr(candles, 14)
    trend = _get_trend(candles, 20)
    price = candles[-1].close
    pip = pip_value(symbol)
    if pip == 0 or atr == 0:
        return []

    d1_bias = d1_data["bias"]
    entries = []

    # Bestimme erlaubte Richtung basierend auf D1 + H4
    allowed_dirs = []
    if d1_bias == "UP":
        allowed_dirs = ["BUY"]
    elif d1_bias == "DOWN":
        allowed_dirs = ["SELL"]
    else:
        # Neutral: H4-Trend entscheidet
        if h4_data["trend"] == "UP": allowed_dirs = ["BUY"]
        elif h4_data["trend"] == "DOWN": allowed_dirs = ["SELL"]
        else: allowed_dirs = ["BUY", "SELL"]   # Beides möglich bei S/R Bounce

    # Pin Bar
    c = candles[-1]
    if c.range > 0.3 * atr and c.body > 0:
        if c.lower_wick > 2.0 * c.body and c.upper_wick < 0.3 * c.range and "BUY" in allowed_dirs:
            entries.append({"pattern": "PinBar", "direction": "BUY"})
        if c.upper_wick > 2.0 * c.body and c.lower_wick < 0.3 * c.range and "SELL" in allowed_dirs:
            entries.append({"pattern": "PinBar", "direction": "SELL"})

    # Engulfing
    if len(candles) >= 3:
        p, c = candles[-2], candles[-1]
        if c.is_bullish and p.is_bearish and c.open <= p.close and c.close >= p.open and c.body > p.body * 1.1:
            if "BUY" in allowed_dirs:
                entries.append({"pattern": "Engulfing", "direction": "BUY"})
        if c.is_bearish and p.is_bullish and c.open >= p.close and c.close <= p.open and c.body > p.body * 1.1:
            if "SELL" in allowed_dirs:
                entries.append({"pattern": "Engulfing", "direction": "SELL"})

    # Inside Bar
    if len(candles) >= 3:
        m, i = candles[-2], candles[-1]
        if i.high < m.high and i.low > m.low and m.range > 0.5 * atr:
            for d in allowed_dirs:
                entries.append({"pattern": "InsideBar", "direction": d})

    # BOS/CHOCH via SMC
    if SMC_AVAILABLE and len(candles) >= 20:
        df = pd.DataFrame({"open": [c.open for c in candles], "high": [c.high for c in candles],
                           "low": [c.low for c in candles], "close": [c.close for c in candles],
                           "volume": [c.volume for c in candles]})
        try:
            sh = smc.swing_highs_lows(df, swing_length=5)
            bos = smc.bos_choch(df, sh)
            for i in range(max(0, len(bos) - 3), len(bos)):
                r = bos.iloc[i]
                if pd.notna(r.get("BOS", None)) and r["BOS"] != 0:
                    d = "BUY" if r["BOS"] > 0 else "SELL"
                    if d in allowed_dirs:
                        entries.append({"pattern": "BOS", "direction": d})
                if pd.notna(r.get("CHOCH", None)) and r["CHOCH"] != 0:
                    d = "BUY" if r["CHOCH"] > 0 else "SELL"
                    if d in allowed_dirs:
                        entries.append({"pattern": "CHOCH", "direction": d})
        except: pass

    return entries


# === Confluence Berechnung ===

def _calc_confluence(direction, d1_bias, h4_trend, h4_at_zone, zone_type, h4_smc, pattern, session):
    score = 1   # Basis: Entry-Signal
    signals = [pattern]

    # D1 Bias
    if (direction == "BUY" and d1_bias == "UP") or (direction == "SELL" and d1_bias == "DOWN"):
        score += 1; signals.append(f"D1_{d1_bias}")

    # H4 Trend
    if (direction == "BUY" and h4_trend == "UP") or (direction == "SELL" and h4_trend == "DOWN"):
        score += 1; signals.append(f"H4_{h4_trend}")

    # H4 an Zone
    if h4_at_zone:
        score += 1; signals.append(zone_type)

    # SMC Signale
    if "FVG" in h4_smc: score += 0.5; signals.append("H4_FVG")
    if "OrderBlock" in h4_smc: score += 0.5; signals.append("H4_OB")

    # Session
    if session in ("LONDON", "NY_OVERLAP"): score += 0.5; signals.append(f"Session_{session}")

    return max(1, min(5, int(round(score)))), signals


# === Haupt-Scan: Top-Down pro Symbol ===

def scan_symbol_topdown(symbol: str) -> list[Setup]:
    """
    Top-Down Scan für ein Symbol:
    D1 → H4 → H1 (nur wenn vorherige Stufe passt)
    """
    pip = pip_value(symbol)
    if pip == 0:
        return []

    session = _session_quality()

    # Step 1: D1 Analyse
    d1 = _analyze_d1(symbol)
    if d1["bias"] == "NEUTRAL" and not d1["supports"] and not d1["resistances"]:
        return []   # Kein klares D1-Bild → Skip

    # Step 2: H4 Analyse
    h4 = _analyze_h4(symbol, d1)

    # Step 3: H1 Entry-Signale (nur wenn H4 an Zone ODER starker Trend)
    if not h4["at_zone"] and d1["bias"] == "NEUTRAL":
        return []   # Kein Trade wenn weder Zone noch Trend

    entries = _find_h1_entries(symbol, d1, h4)
    if not entries:
        return []

    # Chart-Daten für Rendering sammeln
    d1_df = fetch_ohlcv_df(symbol, "D1", 150)
    h4_df = fetch_ohlcv_df(symbol, "H4", 100)
    h1_df = fetch_ohlcv_df(symbol, "H1", 80)

    h1_candles = fetch_candles(symbol, "H1", 80)
    h1_atr = _calc_atr(h1_candles, 14) if len(h1_candles) > 15 else 0
    price = h1_candles[-1].close if h1_candles else 0

    setups = []
    seen = set()

    for entry in entries:
        d = entry["direction"]
        pat = entry["pattern"]
        key = f"{pat}_{d}"
        if key in seen: continue
        seen.add(key)

        # Confluence
        conf, sigs = _calc_confluence(
            d, d1["bias"], h4["trend"], h4["at_zone"],
            h4.get("zone_type", ""), h4.get("smc_signals", []),
            pat, session)

        if conf < MIN_CONFLUENCE:
            continue

        # SL/TP basierend auf H1 ATR
        if h1_atr == 0: continue
        if d == "BUY":
            sl_p = price - h1_atr * 1.2
            tp_p = price + h1_atr * 2.5
        else:
            sl_p = price + h1_atr * 1.2
            tp_p = price - h1_atr * 2.5

        sl_pips = abs(price - sl_p) / pip
        tp_pips = abs(tp_p - price) / pip
        rr = tp_pips / sl_pips if sl_pips > 0 else 0

        if not _validate_sl_tp(symbol, sl_pips, tp_pips):
            continue

        ctx = (f"SYMBOL: {symbol} | D1-BIAS: {d1['bias']} | H4-TREND: {h4['trend']}\n"
               f"H4 AN ZONE: {'JA — ' + h4.get('zone_type','') if h4['at_zone'] else 'NEIN'}\n"
               f"SESSION: {session} | CONFLUENCE: {conf}/5\n"
               f"SIGNALE: {', '.join(sigs)}")

        chart_data = {}
        if d1_df is not None: chart_data["D1"] = d1_df
        if h4_df is not None: chart_data["H4"] = h4_df
        if h1_df is not None: chart_data["H1"] = h1_df

        setups.append(Setup(
            symbol=symbol, timeframe="H1", pattern=pat, direction=d,
            entry_price=round(price, 5), sl_price=round(sl_p, 5), tp_price=round(tp_p, 5),
            sl_pips=round(sl_pips, 1), tp_pips=round(tp_pips, 1), rr=round(rr, 2),
            confluence=conf, signals=sigs, context=ctx, htf_trend=d1["bias"],
            d1_bias=d1["bias"], h4_at_zone=h4["at_zone"],
            chart_data=chart_data, candles=h1_candles[-20:] if h1_candles else []))

    return setups


def fetch_all_d1_data() -> dict[str, pd.DataFrame]:
    """Holt D1-Daten für alle Symbole (für Overview-Grid)."""
    all_data = {}
    for name, mt5_name in SYMBOLS.items():
        info = mt5.symbol_info(mt5_name)
        if info is None: continue
        if not info.visible: mt5.symbol_select(mt5_name, True)
        df = fetch_ohlcv_df(mt5_name, "D1", 100)
        if df is not None and len(df) >= 20:
            all_data[mt5_name] = df
    log.info(f"D1-Daten geladen: {len(all_data)} Symbole")
    return all_data


def get_available_symbols() -> list[str]:
    """Liste aller verfügbaren MT5-Symbole."""
    available = []
    for name, mt5_name in SYMBOLS.items():
        info = mt5.symbol_info(mt5_name)
        if info is not None:
            available.append(mt5_name)
    return available


def scan_all(symbol_filter: list[str] = None) -> list[Setup]:
    """
    Scannt Symbole mit Top-Down Methode. Max 1 Setup pro Symbol.
    
    Args:
        symbol_filter: Wenn gesetzt, nur diese Symbole scannen (Claude's Watchlist)
                       Wenn None, alle Symbole scannen (Fallback)
    """
    all_setups = []

    for name, mt5_name in SYMBOLS.items():
        # Filter: Nur Claude's Auswahl scannen
        if symbol_filter and mt5_name not in symbol_filter:
            continue

        info = mt5.symbol_info(mt5_name)
        if info is None: continue
        if not info.visible: mt5.symbol_select(mt5_name, True)

        try:
            setups = scan_symbol_topdown(mt5_name)
            if setups:
                pattern_prio = {"PinBar": 0, "Engulfing": 1, "BOS": 2, "CHOCH": 3, "InsideBar": 4}
                best = sorted(setups, key=lambda s: (-s.confluence, pattern_prio.get(s.pattern, 9)))[0]
                all_setups.append(best)
        except Exception as e:
            log.error(f"Scan {mt5_name}: {e}")

    all_setups.sort(key=lambda s: -s.confluence)

    high = sum(1 for s in all_setups if s.confluence >= 3)
    filtered = f" (von {len(symbol_filter)} Watchlist)" if symbol_filter else ""
    log.info(f"Scan: {len(all_setups)} Setups{filtered} | {high} Conf>=3")
    return all_setups
