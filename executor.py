# =============================================================================
# executor.py — MT5 Order-Ausführung
# C:\mt5_agent\executor.py
#
# Funktionen:
#   - open_trade()              → Market Order mit SL/TP
#   - close_trade()             → Einzelne Position schließen
#   - modify_sl()               → SL verschieben (Breakeven)
#   - close_all_agent_trades()  → Notfall-Close
#   - Trade-Logging in CSV
# =============================================================================

from __future__ import annotations
import csv
import os
from datetime import datetime
from typing import Optional

import MetaTrader5 as mt5

from config import (
    MAGIC_NUMBERS, TRADE_LOG, DEMO_MODE,
    MIN_SL_PIPS, MAX_SL_PIPS, _auto_pip_value, DEFAULT_MIN_SL, DEFAULT_MAX_SL,
)
from scanner import Setup, pip_value, pips_to_price
from analyzer import Analysis
from logger_setup import get_logger

log = get_logger("executor")


# =============================================================================
# Trade-Logging (CSV)
# =============================================================================

def _ensure_trade_log():
    """Erstellt CSV-Header wenn Datei noch nicht existiert."""
    if os.path.exists(TRADE_LOG):
        return
    try:
        os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
        with open(TRADE_LOG, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "ticket", "symbol", "direction", "lot",
                "entry", "sl", "tp", "sl_pips", "tp_pips", "rr",
                "pattern", "timeframe", "quality", "confidence",
                "reasoning", "magic",
            ])
    except Exception as e:
        log.error(f"Trade-Log erstellen fehlgeschlagen: {e}")


def _log_trade(
    ticket: int, symbol: str, direction: str, lot: float,
    entry: float, sl: float, tp: float,
    sl_pips: float, tp_pips: float, rr: float,
    pattern: str, timeframe: str, quality: str, confidence: int,
    reasoning: str, magic: int,
):
    """Schreibt einen Trade ins CSV-Log."""
    _ensure_trade_log()
    try:
        with open(TRADE_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.utcnow().isoformat(),
                ticket, symbol, direction, lot,
                entry, sl, tp, sl_pips, tp_pips, round(rr, 2),
                pattern, timeframe, quality, confidence,
                reasoning[:150].replace(",", ";"),
                magic,
            ])
    except Exception as e:
        log.error(f"Trade-Log schreiben: {e}")


# =============================================================================
# Order-Ausführung
# =============================================================================

def open_trade(
    setup:        Setup,
    analysis:     Analysis,
    lot_override: Optional[float] = None,
) -> Optional[int]:
    """
    Öffnet eine Market Order basierend auf Setup und Analyse.

    Returns:
        Ticket-Nummer bei Erfolg, None bei Fehler.
    """
    symbol    = setup.symbol
    direction = analysis.direction
    pip       = pip_value(symbol)

    if pip == 0:
        log.error(f"Pip-Value 0 für {symbol} — kein Trade")
        return None

    # Magic Number
    magic = MAGIC_NUMBERS.get(setup.pattern, 21000)

    # Symbol-Info für Präzision und Limits
    info = mt5.symbol_info(symbol)
    if info is None:
        log.error(f"Symbol {symbol} nicht verfügbar")
        return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log.error(f"Kein Tick für {symbol}")
        return None

    # Entry-Preis (Market Order)
    if direction == "BUY":
        entry_price = tick.ask
        sl_price    = entry_price - analysis.sl_pips * pip
        tp_price    = entry_price + analysis.tp_pips * pip
        order_type  = mt5.ORDER_TYPE_BUY
    else:
        entry_price = tick.bid
        sl_price    = entry_price + analysis.sl_pips * pip
        tp_price    = entry_price - analysis.tp_pips * pip
        order_type  = mt5.ORDER_TYPE_SELL

    # Lot
    lot = lot_override if lot_override else info.volume_min

    # SL/TP auf richtige Dezimalstellen runden
    digits = info.digits
    sl_price    = round(sl_price, digits)
    tp_price    = round(tp_price, digits)
    entry_price = round(entry_price, digits)

    # --- Order senden ---
    request = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    symbol,
        "volume":    lot,
        "type":      order_type,
        "price":     entry_price,
        "sl":        sl_price,
        "tp":        tp_price,
        "deviation": 20,   # Slippage in Points
        "magic":     magic,
        "comment":   f"AI_{setup.pattern}_{setup.timeframe}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling_type(symbol),
    }

    log.info(
        f"Order senden: {symbol} {direction} {lot} Lot | "
        f"Entry:{entry_price} SL:{sl_price} TP:{tp_price} | "
        f"Magic:{magic}"
    )

    result = mt5.order_send(request)

    if result is None:
        log.error(f"order_send() returned None: {mt5.last_error()}")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error(
            f"Order fehlgeschlagen: {result.retcode} | "
            f"Comment: {result.comment} | {mt5.last_error()}"
        )
        return None

    ticket = result.order
    log.info(f"Order erfolgreich: #{ticket} {symbol} {direction} {lot} Lot")

    # CSV-Log
    rr = analysis.tp_pips / analysis.sl_pips if analysis.sl_pips > 0 else 0
    _log_trade(
        ticket=ticket, symbol=symbol, direction=direction, lot=lot,
        entry=entry_price, sl=sl_price, tp=tp_price,
        sl_pips=analysis.sl_pips, tp_pips=analysis.tp_pips, rr=rr,
        pattern=setup.pattern, timeframe=setup.timeframe,
        quality=analysis.quality, confidence=analysis.confidence,
        reasoning=analysis.reasoning, magic=magic,
    )

    return ticket


def _get_filling_type(symbol: str) -> int:
    """Bestimmt den richtigen Filling-Modus für den Broker."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC

    filling = info.filling_mode

    # Bit-Flags: FOK=1, IOC=2, RETURN=4 (nicht alle MT5-Versionen haben SYMBOL_FILLING_*)
    if filling & 1:   # FOK
        return mt5.ORDER_FILLING_FOK
    elif filling & 2:  # IOC
        return mt5.ORDER_FILLING_IOC
    else:
        return mt5.ORDER_FILLING_RETURN


# =============================================================================
# Position modifizieren
# =============================================================================

def modify_sl(ticket: int, new_sl: float) -> bool:
    """Verschiebt den SL einer offenen Position (z.B. Breakeven)."""
    position = mt5.positions_get(ticket=ticket)
    if not position:
        log.warning(f"Position #{ticket} nicht gefunden für SL-Modify")
        return False

    pos = position[0]
    info = mt5.symbol_info(pos.symbol)
    digits = info.digits if info else 5
    new_sl = round(new_sl, digits)

    # === Validierung: SL darf nicht auf der falschen Seite vom TP liegen ===
    if pos.tp > 0:
        if pos.type == mt5.POSITION_TYPE_BUY:
            # BUY: SL muss UNTER Entry/Preis sein, nie über TP
            if new_sl >= pos.tp:
                log.warning(f"SL-Validierung #{ticket} BUY: SL {new_sl} >= TP {pos.tp} — überspringe")
                return False
        else:
            # SELL: SL muss ÜBER Entry/Preis sein, nie unter TP
            if new_sl <= pos.tp:
                log.warning(f"SL-Validierung #{ticket} SELL: SL {new_sl} <= TP {pos.tp} — überspringe")
                return False

    # SL darf nicht weiter vom Preis weg als der aktuelle SL (nur enger trailing)
    if pos.sl > 0:
        if pos.type == mt5.POSITION_TYPE_BUY and new_sl < pos.sl:
            log.debug(f"SL #{ticket} BUY: Neuer SL {new_sl} < alter SL {pos.sl} — überspringe (nur trailing)")
            return False
        if pos.type == mt5.POSITION_TYPE_SELL and new_sl > pos.sl:
            log.debug(f"SL #{ticket} SELL: Neuer SL {new_sl} > alter SL {pos.sl} — überspringe (nur trailing)")
            return False

    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol":   pos.symbol,
        "sl":       new_sl,
        "tp":       pos.tp,
        "magic":    pos.magic,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"SL-Modify #{ticket} fehlgeschlagen: {result}")
        return False

    log.info(f"SL geändert #{ticket}: {pos.sl} → {new_sl}")
    return True


# =============================================================================
# Position schließen
# =============================================================================

def close_trade(ticket: int, reason: str = "manual") -> bool:
    """Schließt eine einzelne Position. Versucht verschiedene Filling-Modi."""
    position = mt5.positions_get(ticket=ticket)
    if not position:
        log.warning(f"Position #{ticket} nicht gefunden zum Schließen")
        return False

    pos = position[0]
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        log.error(f"Kein Tick für {pos.symbol}")
        return False

    # Gegenorder
    if pos.type == mt5.POSITION_TYPE_BUY:
        close_type  = mt5.ORDER_TYPE_SELL
        close_price = tick.bid
    else:
        close_type  = mt5.ORDER_TYPE_BUY
        close_price = tick.ask

    # Versuche verschiedene Filling-Modi
    filling_modes = [
        _get_filling_type(pos.symbol),
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_RETURN,
    ]
    # Deduplizieren, Reihenfolge beibehalten
    seen = set()
    unique_fillings = []
    for f in filling_modes:
        if f not in seen:
            seen.add(f)
            unique_fillings.append(f)

    for filling in unique_fillings:
        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "position":  ticket,
            "symbol":    pos.symbol,
            "volume":    pos.volume,
            "type":      close_type,
            "price":     close_price,
            "deviation": 30,
            "magic":     pos.magic,
            "comment":   f"AI_close_{reason[:20]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"Position #{ticket} geschlossen ({reason})")
            return True

        err = mt5.last_error() if result is None else f"retcode={result.retcode} comment={result.comment}"
        log.debug(f"Close #{ticket} filling={filling}: {err}")

    log.error(f"Close #{ticket} fehlgeschlagen nach allen Filling-Modi | last_error={mt5.last_error()}")
    return False


def close_all_agent_trades(reason: str = "emergency") -> int:
    """Schließt ALLE Agent-Positionen. Gibt Anzahl geschlossener Trades zurück."""
    agent_magics = set(MAGIC_NUMBERS.values())
    positions    = mt5.positions_get() or []
    agent_pos    = [p for p in positions if p.magic in agent_magics]

    closed = 0
    for pos in agent_pos:
        if close_trade(pos.ticket, reason):
            closed += 1

    log.info(f"close_all_agent_trades({reason}): {closed}/{len(agent_pos)} geschlossen")
    return closed
