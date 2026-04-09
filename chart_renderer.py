# =============================================================================
# chart_renderer.py — Chart-Bilder für Claude Vision Analyse
# C:\mt5_agent\chart_renderer.py
#
# Generiert saubere Candlestick-Charts als PNG:
#   - D1 Chart (150 Kerzen) → Großes Bild, Trend, Major S/R
#   - H4 Chart (100 Kerzen) → Struktur, Zonen, Order Blocks
#   - H1 Chart (80 Kerzen)  → Entry-Timing, Candle-Patterns
#
# Charts enthalten: Candles, Volume, EMA20/50, S/R Levels
# Claude Vision analysiert die Bilder wie ein Trader den Chart liest.
# =============================================================================

from __future__ import annotations
import os
import tempfile
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # Kein Display nötig (Server/VPS)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplfinance as mpf

from logger_setup import get_logger

log = get_logger("chart")

CHART_DIR = r"C:\mt5_agent\charts"

# Chart-Konfiguration pro Timeframe
TF_CONFIG = {
    "D1":  {"bars": 150, "title_suffix": "Daily",  "ema_fast": 20, "ema_slow": 50},
    "W1":  {"bars": 80,  "title_suffix": "Weekly", "ema_fast": 10, "ema_slow": 30},
    "H4":  {"bars": 100, "title_suffix": "4H",     "ema_fast": 20, "ema_slow": 50},
    "H1":  {"bars": 80,  "title_suffix": "1H",     "ema_fast": 20, "ema_slow": 50},
    "M15": {"bars": 80,  "title_suffix": "15M",    "ema_fast": 12, "ema_slow": 26},
}

# Sauberer Chart-Style (dunkel, gut lesbar für Claude)
CHART_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="lime", down="red",
        edge={"up": "lime", "down": "red"},
        wick={"up": "lime", "down": "red"},
        volume={"up": "lime", "down": "red"},
    ),
    rc={
        "font.size": 10,
        "axes.labelsize": 10,
        "xtick.labelsize": 8,
        "ytick.labelsize": 9,
    },
)


def _ensure_chart_dir():
    os.makedirs(CHART_DIR, exist_ok=True)


def _find_sr_levels(df: pd.DataFrame, lookback: int = 50) -> tuple[list[float], list[float]]:
    """Findet S/R Levels für horizontale Linien im Chart."""
    supports, resistances = [], []
    highs = df["high"].values
    lows = df["low"].values
    n = min(lookback, len(df))

    for i in range(2, n - 2):
        idx = len(df) - n + i
        if (lows[idx] < lows[idx-1] and lows[idx] < lows[idx-2]
            and lows[idx] < lows[idx+1] and lows[idx] < lows[idx+2]):
            supports.append(lows[idx])
        if (highs[idx] > highs[idx-1] and highs[idx] > highs[idx-2]
            and highs[idx] > highs[idx+1] and highs[idx] > highs[idx+2]):
            resistances.append(highs[idx])

    # Clustere nahe Levels (innerhalb 0.3% voneinander)
    supports = _cluster_levels(supports, df["close"].iloc[-1])
    resistances = _cluster_levels(resistances, df["close"].iloc[-1])

    return supports[-4:], resistances[-4:]   # Max 4 pro Seite


def _cluster_levels(levels: list[float], current_price: float, threshold_pct: float = 0.003) -> list[float]:
    """Gruppiert nahe beieinander liegende Levels."""
    if not levels:
        return []
    levels = sorted(levels)
    clustered = [levels[0]]
    for level in levels[1:]:
        if abs(level - clustered[-1]) / current_price > threshold_pct:
            clustered.append(level)
        else:
            clustered[-1] = (clustered[-1] + level) / 2   # Durchschnitt
    return clustered


def render_chart(
    symbol: str,
    timeframe: str,
    ohlcv_data: pd.DataFrame,
    sr_supports: list[float] = None,
    sr_resistances: list[float] = None,
    entry_price: float = None,
    sl_price: float = None,
    tp_price: float = None,
) -> Optional[str]:
    """
    Rendert einen Candlestick-Chart als PNG.

    Args:
        symbol: z.B. "EURUSD"
        timeframe: z.B. "H4"
        ohlcv_data: DataFrame mit open/high/low/close/volume + DatetimeIndex
        sr_supports: Horizontale Support-Linien
        sr_resistances: Horizontale Resistance-Linien
        entry_price: Markierung für Entry
        sl_price: Markierung für SL
        tp_price: Markierung für TP

    Returns:
        Pfad zur PNG-Datei oder None bei Fehler
    """
    _ensure_chart_dir()

    try:
        cfg = TF_CONFIG.get(timeframe, TF_CONFIG["H1"])
        df = ohlcv_data.copy()

        # Sicherstellen dass Index DatetimeIndex ist
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, unit="s")

        # EMAs berechnen
        ema_fast = df["close"].ewm(span=cfg["ema_fast"], adjust=False).mean()
        ema_slow = df["close"].ewm(span=cfg["ema_slow"], adjust=False).mean()

        add_plots = [
            mpf.make_addplot(ema_fast, color="cyan", width=1.0, label=f"EMA{cfg['ema_fast']}"),
            mpf.make_addplot(ema_slow, color="orange", width=1.0, label=f"EMA{cfg['ema_slow']}"),
        ]

        # S/R Levels als horizontale Linien
        hlines = {"hlines": [], "colors": [], "linestyle": "--", "linewidths": 0.8}

        if sr_supports:
            for s in sr_supports:
                hlines["hlines"].append(s)
                hlines["colors"].append("green")
        if sr_resistances:
            for r in sr_resistances:
                hlines["hlines"].append(r)
                hlines["colors"].append("red")

        # Entry/SL/TP Linien
        if entry_price:
            hlines["hlines"].append(entry_price)
            hlines["colors"].append("white")
        if sl_price:
            hlines["hlines"].append(sl_price)
            hlines["colors"].append("red")
        if tp_price:
            hlines["hlines"].append(tp_price)
            hlines["colors"].append("lime")

        # Chart titel
        title = f"{symbol} {cfg['title_suffix']} | {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"

        # Dateiname
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
        filename = f"{symbol}_{timeframe}_{ts}.png"
        filepath = os.path.join(CHART_DIR, filename)

        # Rendern
        kwargs = {
            "type": "candle",
            "style": CHART_STYLE,
            "title": title,
            "volume": True,
            "addplot": add_plots,
            "figsize": (14, 8),
            "tight_layout": True,
            "savefig": {"fname": filepath, "dpi": 100, "bbox_inches": "tight"},
        }

        if hlines["hlines"]:
            kwargs["hlines"] = hlines

        mpf.plot(df, **kwargs)
        plt.close("all")

        log.debug(f"Chart gespeichert: {filepath}")
        return filepath

    except Exception as e:
        log.error(f"Chart-Render Fehler {symbol} {timeframe}: {e}")
        plt.close("all")
        return None


def render_multi_tf_charts(
    symbol: str,
    candle_data: dict,   # {"D1": df, "H4": df, "H1": df}
) -> dict[str, str]:
    """
    Rendert Charts für mehrere Timeframes.

    Args:
        symbol: z.B. "EURUSD"
        candle_data: Dict mit Timeframe → OHLCV DataFrame

    Returns:
        Dict mit Timeframe → Dateipfad
    """
    charts = {}

    for tf, df in candle_data.items():
        if df is None or len(df) < 20:
            continue

        # S/R Levels berechnen
        sups, ress = _find_sr_levels(df)

        filepath = render_chart(symbol, tf, df, sr_supports=sups, sr_resistances=ress)
        if filepath:
            charts[tf] = filepath

    return charts


def cleanup_old_charts(max_age_hours: int = 24):
    """Löscht Charts älter als N Stunden."""
    if not os.path.exists(CHART_DIR):
        return
    import time
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    deleted = 0
    for f in os.listdir(CHART_DIR):
        fp = os.path.join(CHART_DIR, f)
        if os.path.isfile(fp) and f.endswith(".png"):
            if os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                deleted += 1
    if deleted > 0:
        log.debug(f"Alte Charts gelöscht: {deleted}")


def render_overview_grid(
    symbols_data: dict[str, pd.DataFrame],
    columns: int = 5,
) -> Optional[str]:
    """
    Rendert eine Übersicht aller D1-Charts als Grid.
    Claude bekommt EIN Bild und wählt interessante Paare aus.

    Args:
        symbols_data: Dict Symbol → D1 OHLCV DataFrame
        columns: Spalten im Grid (5 = 6 Reihen für 30 Paare)

    Returns:
        Pfad zur PNG-Datei
    """
    _ensure_chart_dir()

    symbols = list(symbols_data.keys())
    n = len(symbols)
    if n == 0:
        return None

    rows = (n + columns - 1) // columns

    try:
        fig, axes = plt.subplots(rows, columns, figsize=(columns * 4, rows * 2.2))
        fig.patch.set_facecolor("black")

        # Flatten axes array
        if rows == 1 and columns == 1:
            axes = np.array([[axes]])
        elif rows == 1:
            axes = axes.reshape(1, -1)
        elif columns == 1:
            axes = axes.reshape(-1, 1)

        for idx, symbol in enumerate(symbols):
            row = idx // columns
            col = idx % columns
            ax = axes[row][col]

            df = symbols_data[symbol]
            if df is None or len(df) < 10:
                ax.set_facecolor("black")
                ax.text(0.5, 0.5, f"{symbol}\nNo Data", color="gray",
                        ha="center", va="center", transform=ax.transAxes, fontsize=8)
                ax.set_xticks([])
                ax.set_yticks([])
                continue

            # Mini-Candlestick als Linien-Chart (schneller + lesbarer bei kleiner Größe)
            closes = df["close"].values
            highs = df["high"].values
            lows = df["low"].values

            # EMA20 und EMA50
            ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().values
            ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().values

            x = range(len(closes))

            ax.set_facecolor("black")
            ax.plot(x, closes, color="white", linewidth=0.8, alpha=0.9)
            ax.plot(x, ema20, color="cyan", linewidth=0.5, alpha=0.7)
            ax.plot(x, ema50, color="orange", linewidth=0.5, alpha=0.7)

            # Farbige Füllung zwischen Preis und EMA50
            ax.fill_between(x, closes, ema50,
                           where=[c > e for c, e in zip(closes, ema50)],
                           color="green", alpha=0.15)
            ax.fill_between(x, closes, ema50,
                           where=[c < e for c, e in zip(closes, ema50)],
                           color="red", alpha=0.15)

            # Aktueller Preis-Level
            last_close = closes[-1]
            ax.axhline(y=last_close, color="yellow", linewidth=0.3, alpha=0.5)

            # Trend-Pfeil
            if len(closes) > 20:
                trend_start = np.mean(closes[-20:-10])
                trend_end = np.mean(closes[-10:])
                if trend_end > trend_start * 1.001:
                    trend_color = "lime"
                    trend_symbol = "▲"
                elif trend_end < trend_start * 0.999:
                    trend_color = "red"
                    trend_symbol = "▼"
                else:
                    trend_color = "gray"
                    trend_symbol = "►"
            else:
                trend_color = "gray"
                trend_symbol = "?"

            ax.set_title(f"{symbol} {trend_symbol}", color=trend_color,
                        fontsize=9, fontweight="bold", pad=2)
            ax.set_xticks([])
            ax.set_yticks([])

            for spine in ax.spines.values():
                spine.set_color("dimgray")
                spine.set_linewidth(0.5)

        # Leere Zellen verstecken
        for idx in range(n, rows * columns):
            row = idx // columns
            col = idx % columns
            axes[row][col].set_visible(False)

        fig.suptitle(
            f"D1 OVERVIEW — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC — Which pairs look interesting?",
            color="gold", fontsize=12, fontweight="bold", y=0.98
        )

        plt.tight_layout(rect=[0, 0, 1, 0.96])

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(CHART_DIR, f"overview_{ts}.png")
        fig.savefig(filepath, dpi=120, bbox_inches="tight", facecolor="black")
        plt.close(fig)

        log.info(f"Overview-Grid: {n} Symbole → {filepath}")
        return filepath

    except Exception as e:
        log.error(f"Overview-Grid Fehler: {e}", exc_info=True)
        plt.close("all")
        return None
