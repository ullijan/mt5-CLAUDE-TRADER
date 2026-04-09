# =============================================================================
# lot_calculator.py — ATR-basierte dynamische Lot-Berechnung (v4)
# C:\mt5_agent\lot_calculator.py
#
# v4 Fixes:
#   - Per-Symbol ATR-Schwellwerte aus config.py (Gold/DAX brauchen andere als Forex)
#   - Dollar-Risk-Cap: Lot wird reduziert wenn Risiko > MAX_RISK_PER_TRADE_PCT
# =============================================================================

from __future__ import annotations
import MetaTrader5 as mt5
import numpy as np

from config import (
    LOT_SIZES, ATR_VOL_THRESHOLDS, ATR_VOL_DEFAULT,
    MAX_RISK_PER_TRADE_PCT, PIP_DEFINITIONS,
)
from scanner import fetch_candles
from logger_setup import get_logger

log = get_logger("lot_calc")

ATR_PERIOD = 14

# Lot-Anpassung bei Volatilität
HIGH_VOL_LOT_FACTOR = 0.70   # -30% bei hoher Vola
LOW_VOL_LOT_FACTOR  = 1.10   # +10% bei niedriger Vola


def _calc_atr(candles, period: int = ATR_PERIOD) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, period + 1):
        c = candles[-i]
        p = candles[-i - 1]
        tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        trs.append(tr)
    return float(np.mean(trs)) if trs else 0.0


def _atr_lot_factor(symbol: str, timeframe: str = "H1") -> float:
    """Volatilitäts-Faktor mit per-Symbol Schwellwerten."""
    candles = fetch_candles(symbol, timeframe, ATR_PERIOD + 10)
    if not candles:
        return 1.0

    atr = _calc_atr(candles, ATR_PERIOD)
    if atr == 0:
        return 1.0

    current_price = candles[-1].close
    if current_price == 0:
        return 1.0

    atr_pct = atr / current_price

    # Per-Symbol Schwellwerte
    thresholds = ATR_VOL_THRESHOLDS.get(symbol, ATR_VOL_DEFAULT)
    high_thresh = thresholds["high"]
    low_thresh  = thresholds["low"]

    if atr_pct >= high_thresh:
        factor = HIGH_VOL_LOT_FACTOR
        log.debug(f"{symbol} ATR {atr_pct*100:.3f}% >= {high_thresh*100:.1f}% → HOCH → {factor}")
    elif atr_pct <= low_thresh:
        factor = LOW_VOL_LOT_FACTOR
        log.debug(f"{symbol} ATR {atr_pct*100:.3f}% <= {low_thresh*100:.1f}% → niedrig → {factor}")
    else:
        ratio  = (atr_pct - low_thresh) / (high_thresh - low_thresh)
        factor = LOW_VOL_LOT_FACTOR + ratio * (HIGH_VOL_LOT_FACTOR - LOW_VOL_LOT_FACTOR)
        log.debug(f"{symbol} ATR {atr_pct*100:.3f}% → Faktor {factor:.2f}")

    return round(max(0.50, min(1.20, factor)), 2)


def _round_lot(symbol: str, raw_lot: float) -> float:
    """Rundet Lot auf Broker-Lot-Step und prüft Min/Max."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return round(max(0.01, raw_lot), 2)

    lot_min  = info.volume_min
    lot_max  = info.volume_max
    lot_step = info.volume_step

    if lot_step > 0:
        steps = round(raw_lot / lot_step)
        lot   = steps * lot_step
    else:
        lot = raw_lot

    lot = max(lot_min, min(lot_max, lot))
    return round(lot, 8)


def _cap_lot_by_risk(symbol: str, raw_lot: float, sl_pips: float = 30) -> float:
    """
    Reduziert Lot wenn Dollar-Risiko > MAX_RISK_PER_TRADE_PCT.
    Annäherung — exaktere Berechnung im RiskManager.
    """
    acc = mt5.account_info()
    if not acc or acc.balance <= 0:
        return raw_lot

    max_risk_usd = acc.balance * MAX_RISK_PER_TRADE_PCT / 100.0
    pip = PIP_DEFINITIONS.get(symbol, 0.0001)

    info = mt5.symbol_info(symbol)
    if info and info.trade_contract_size > 0:
        pip_value_per_lot = pip * info.trade_contract_size
    else:
        pip_value_per_lot = pip * 100000

    if pip_value_per_lot <= 0 or sl_pips <= 0:
        return raw_lot

    risk_at_current_lot = sl_pips * pip_value_per_lot * raw_lot
    if risk_at_current_lot <= max_risk_usd:
        return raw_lot

    # Lot reduzieren
    max_lot = max_risk_usd / (sl_pips * pip_value_per_lot)
    log.info(
        f"Lot-Cap {symbol}: ${risk_at_current_lot:.2f} > ${max_risk_usd:.2f} max → "
        f"Lot {raw_lot:.3f} → {max_lot:.3f}"
    )
    return max_lot


def calculate_lot(
    symbol:        str,
    pattern:       str,
    timeframe:     str,
    memory_factor: float = 1.0,
    sl_pips:       float = 30,
) -> float:
    """
    Berechnet finale Lot-Größe.

    Faktoren:
    1. Basis-Lot (config)
    2. × ATR-Faktor (Volatilität)
    3. × Memory-Faktor (Performance)
    4. Dollar-Risk-Cap
    5. Broker-Rounding
    """
    base_lot   = LOT_SIZES.get(symbol, 0.01)
    atr_factor = _atr_lot_factor(symbol, "H1")

    raw_lot = base_lot * atr_factor * memory_factor

    # Dollar-Cap
    capped_lot = _cap_lot_by_risk(symbol, raw_lot, sl_pips)

    # Broker-Rounding
    final_lot = _round_lot(symbol, capped_lot)

    log.info(
        f"Lot {symbol} {pattern} {timeframe}: "
        f"Base {base_lot:.2f} × ATR {atr_factor:.2f} × Mem {memory_factor:.2f} "
        f"= {raw_lot:.3f} → Cap {capped_lot:.3f} → Final {final_lot}"
    )

    return final_lot
