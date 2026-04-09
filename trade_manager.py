# =============================================================================
# trade_manager.py — Reines Python Position-Management (v8)
# C:\mt5_agent\trade_manager.py
#
# v8: KEIN Claude Vision mehr. Alles regelbasiert in Python.
# Spart ~80% der Management-API-Kosten.
#
# Regeln:
#   1. Breakeven: Wenn Profit >= 50% des TP-Abstands → SL auf Entry + Buffer
#   2. Trailing:  Wenn Profit >= 75% des TP-Abstands → SL enger (Swing-Level)
#   3. Zeit-Stop: Wenn Trade > 48h und Profit < 25% → Close
# =============================================================================

from __future__ import annotations
import time

import MetaTrader5 as mt5

from config import (
    MAGIC_NUMBERS, _auto_pip_value,
    BE_TRIGGER_PCT, BE_OFFSET_PIPS,
)
from executor import close_trade, modify_sl
from logger_setup import get_logger

log = get_logger("trade_mgr")

# Trailing-Trigger: SL enger ziehen ab 75% TP
TRAIL_TRIGGER_PCT = 0.75
# Trail-Abstand: SL bleibt X Pips hinter aktuellem Preis
TRAIL_OFFSET_ATR = 0.8   # 80% des ATR als Trail-Abstand

# Zeit-Stop: Trade > 48h und < 25% Profit → Close
TIME_STOP_HOURS = 48
TIME_STOP_MIN_PROFIT_PCT = 0.25


def _get_agent_positions() -> list:
    agent_magics = set(MAGIC_NUMBERS.values())
    positions = mt5.positions_get() or []
    return [p for p in positions if p.magic in agent_magics]


def _calc_h1_atr(symbol: str) -> float:
    """Berechnet H1 ATR(14) für Trail-Abstand."""
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 20)
        if rates is None or len(rates) < 15:
            return 0.0
        trs = []
        for i in range(1, 15):
            h = float(rates[-i][2])   # High
            l = float(rates[-i][3])   # Low
            c = float(rates[-i-1][4]) # Prev Close
            tr = max(h - l, abs(h - c), abs(l - c))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0.0
    except Exception:
        return 0.0


def manage_all_trades() -> dict:
    """
    Haupt-Funktion: Reines Python-Management für alle offenen Trades.
    Kein Claude Vision, keine API-Calls.
    """
    positions = _get_agent_positions()
    stats = {"managed": len(positions), "be_set": 0, "closed": 0, "adjusted": 0}

    if not positions:
        log.debug("Keine offenen Agent-Trades")
        return stats

    log.info(f"Manage: {len(positions)} offene Trades")

    for pos in positions:
        try:
            # Position muss mindestens 15 Min alt sein
            if time.time() - pos.time < 900:
                continue

            pip = _auto_pip_value(pos.symbol)
            if pip == 0:
                continue

            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                continue

            current = tick.bid if is_buy else tick.ask
            entry = pos.price_open
            sl = pos.sl
            tp = pos.tp

            if tp == 0 or sl == 0:
                continue

            # Distanzen berechnen
            if is_buy:
                tp_dist = tp - entry
                profit_dist = current - entry
            else:
                tp_dist = entry - tp
                profit_dist = entry - current

            if tp_dist <= 0:
                continue

            progress_pct = profit_dist / tp_dist
            profit_pips = profit_dist / pip
            trade_age_hours = (time.time() - pos.time) / 3600

            # === REGEL 1: Breakeven bei 50% TP ===
            # BE-Offset: symbolspezifisch (Spread×1.5, nicht feste 2 Pips)
            if progress_pct >= BE_TRIGGER_PCT:
                spread = tick.ask - tick.bid
                be_offset = max(spread * 1.5, 2.0 * pip)  # Min 2 Pips oder 1.5× Spread
                be_price = entry + be_offset if is_buy else entry - be_offset

                should_be = (is_buy and sl < entry) or (not is_buy and sl > entry)
                if should_be:
                    if modify_sl(pos.ticket, round(be_price, 5)):
                        stats["be_set"] += 1
                        log.info(f"  BE #{pos.ticket} {pos.symbol}: SL → {be_price:.5f} (Spread: {spread/pip:.1f}p)")

            # === KEIN TRAILING (Phase 1: saubere Daten) ===
            # Trade läuft bis TP oder BE — kein Abschneiden der Gewinne
            # MFE (Max Favorable Excursion) wird geloggt für spätere Trailing-Analyse

            # === MFE Shadow-Logging (kein Einfluss auf Trade) ===
            try:
                import json, os
                MFE_FILE = r"C:\mt5_agent\mfe_log.json"
                mfe_data = {}
                if os.path.exists(MFE_FILE):
                    with open(MFE_FILE, "r") as f:
                        mfe_data = json.load(f)
                
                ticket_key = str(pos.ticket)
                prev_mfe = mfe_data.get(ticket_key, {}).get("max_profit_pips", 0)
                if profit_pips > prev_mfe:
                    mfe_data[ticket_key] = {
                        "symbol": pos.symbol,
                        "direction": "BUY" if is_buy else "SELL",
                        "max_profit_pips": round(profit_pips, 1),
                        "tp_pips": round(tp_dist / pip, 1),
                        "progress_pct": round(progress_pct * 100, 1),
                    }
                    with open(MFE_FILE, "w") as f:
                        json.dump(mfe_data, f, indent=2)
            except Exception:
                pass  # MFE-Logging darf nie den Trade beeinflussen

            # === REGEL 2: Zeit-Stop bei > 48h und < 25% Profit ===
            if trade_age_hours > TIME_STOP_HOURS and progress_pct < TIME_STOP_MIN_PROFIT_PCT:
                log.info(f"  Zeit-Stop #{pos.ticket} {pos.symbol}: {trade_age_hours:.0f}h alt, nur {progress_pct*100:.0f}% Profit")
                if close_trade(pos.ticket, reason=f"time_stop_{trade_age_hours:.0f}h"):
                    stats["closed"] += 1

        except Exception as e:
            log.error(f"Manage #{pos.ticket}: {e}", exc_info=True)

    if stats["closed"] > 0 or stats["adjusted"] > 0 or stats["be_set"] > 0:
        log.info(f"Management: BE: {stats['be_set']} | Trail: {stats['adjusted']} | Close: {stats['closed']}")

    return stats
