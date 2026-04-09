# =============================================================================
# agent.py — Autonomer AI Trading Agent v4
# C:\mt5_agent\agent.py
#
# v4 Fixes:
#   - Account-Check beim Start (EXPECTED_ACCOUNT)
#   - DEMO_MODE Flag
#   - Dollar-Risk Validierung vor Trade
#   - Cooldown-Recording bei Close
#   - Saubere Meta-Analyse Timing (kein Trigger bei Stunde 0)
#
# Start:    py -3.11 agent.py
# Stopp:    Strg+C
# Notfall:  py -3.11 agent.py --close-all
# Report:   py -3.11 agent.py --report
# Meta:     py -3.11 agent.py --meta
# =============================================================================

from __future__ import annotations
import sys
import time
import json
import signal
import argparse
import datetime as dt
from datetime import datetime, timedelta, date

import MetaTrader5 as mt5

from config import (
    MT5_TERMINAL_PATH, MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER,
    EXPECTED_ACCOUNT, DEMO_MODE,
    SCAN_INTERVAL_SEC, MANAGE_INTERVAL_SEC,
    STATE_FILE, MIN_QUALITY, MIN_RR_RATIO, ANTHROPIC_API_KEY, MAGIC_NUMBERS,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    NOTIFY_TRADE_OPEN, NOTIFY_TRADE_CLOSE, NOTIFY_BREAKEVEN,
    NOTIFY_BLACKLIST, NOTIFY_DD_WARNING, NOTIFY_DAILY_SUMMARY, NOTIFY_META_ANALYSIS,
    META_ANALYSIS_HOUR_UTC, DAILY_DD_LIMIT_PCT, _auto_pip_value,
    MAX_OPEN_TRADES,
    MARKET_OPEN_DAY, MARKET_OPEN_HOUR, MARKET_CLOSE_DAY, MARKET_CLOSE_HOUR,
)
from scanner        import scan_all, Setup, pip_value
from analyzer       import analyze_setup_with_charts as analyze_setup
from executor       import open_trade, close_all_agent_trades
from risk_manager   import RiskManager
from trade_manager  import manage_all_trades
from memory         import get_memory
from lot_calculator import calculate_lot
import telegram_notify as tg
from meta_analyzer  import run_meta_analysis, send_daily_summary
from logger_setup   import get_logger

log = get_logger("agent")
_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown-Signal — Agent wird beendet")
    _running = False


# -----------------------------------------------------------------------------
# Markt-Öffnungszeiten Check
# -----------------------------------------------------------------------------

def is_market_open() -> bool:
    """
    Prüft ob Forex/Gold Märkte geöffnet sind.
    Offen: Sonntag 21:00 UTC bis Freitag 22:00 UTC
    Geschlossen: Freitag 22:00 → Sonntag 21:00
    """
    now = datetime.utcnow()
    weekday = now.weekday()   # 0=Mo, 4=Fr, 5=Sa, 6=So
    hour    = now.hour

    # Samstag: immer zu
    if weekday == 5:
        return False

    # Sonntag: erst ab MARKET_OPEN_HOUR (21:00)
    if weekday == MARKET_OPEN_DAY and hour < MARKET_OPEN_HOUR:
        return False

    # Freitag: nach MARKET_CLOSE_HOUR (22:00) zu
    if weekday == MARKET_CLOSE_DAY and hour >= MARKET_CLOSE_HOUR:
        return False

    return True


# -----------------------------------------------------------------------------
# MT5 Verbindung (mit Account-Check)
# -----------------------------------------------------------------------------

def connect_mt5() -> bool:
    ok = mt5.initialize(path=MT5_TERMINAL_PATH) if MT5_TERMINAL_PATH else mt5.initialize()
    if not ok:
        log.error(f"MT5 initialize() fehlgeschlagen: {mt5.last_error()}")
        return False

    if MT5_ACCOUNT and MT5_PASSWORD and MT5_SERVER:
        if not mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER):
            log.error(f"MT5 Login fehlgeschlagen: {mt5.last_error()}")
            mt5.shutdown()
            return False

    acc = mt5.account_info()
    if not acc:
        log.error("Kein Account-Info verfügbar")
        mt5.shutdown()
        return False

    # ACCOUNT-CHECK
    if EXPECTED_ACCOUNT > 0 and acc.login != EXPECTED_ACCOUNT:
        log.error(
            f"FALSCHER ACCOUNT! Eingeloggt: #{acc.login} ({acc.server}) | "
            f"Erwartet: #{EXPECTED_ACCOUNT} — AGENT WIRD NICHT GESTARTET!"
        )
        tg.notify_error("Account-Check", f"Falscher Account #{acc.login} statt #{EXPECTED_ACCOUNT}")
        mt5.shutdown()
        return False

    mode = "DEMO" if DEMO_MODE else "LIVE"
    log.info(f"MT5: #{acc.login} | {acc.server} | {acc.balance:.2f} {acc.currency} | {mode}")

    if NOTIFY_TRADE_OPEN:
        tg.notify_agent_start(acc.balance, acc.currency, acc.login)

    return True


# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------

def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"open_tickets": {}, "last_meta_date": "", "last_summary_date": ""}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.error(f"State-Save: {e}")


# -----------------------------------------------------------------------------
# Trade-Monitoring
# -----------------------------------------------------------------------------

def _get_closed_pips(ticket: int, info: dict) -> float | None:
    """Berechnet Pips eines geschlossenen Trades aus der Deal-History."""
    from datetime import timezone
    symbol = info.get("symbol", "")
    pip = pip_value(symbol) if symbol else 0

    # Suche Deals für diese Position
    try:
        date_from = dt.datetime.now(timezone.utc) - timedelta(days=14)
        date_to = dt.datetime.now(timezone.utc) + timedelta(days=1)
        all_deals = mt5.history_deals_get(date_from, date_to)
        if not all_deals:
            log.debug(f"Pips #{ticket}: Keine Deals gefunden")
            return None

        pos_deals = [d for d in all_deals if d.position_id == ticket]
        if not pos_deals:
            log.debug(f"Pips #{ticket}: Keine Deals für Position")
            return None

        open_d = next((d for d in pos_deals if d.entry == mt5.DEAL_ENTRY_IN), None)
        close_d = next((d for d in pos_deals if d.entry == mt5.DEAL_ENTRY_OUT), None)

        # Methode 1: Preis-Differenz aus Open/Close Deals
        if open_d and close_d and pip > 0:
            diff = close_d.price - open_d.price
            if open_d.type == mt5.DEAL_TYPE_SELL:
                diff = -diff
            pips = round(diff / pip, 1)

            # Sanity-Check: Pips sollten realistisch sein (-500 bis +500 für Forex, -5000 bis +5000 für Gold)
            max_pips = 5000 if "XAU" in symbol else 500
            if abs(pips) <= max_pips:
                log.info(f"Pips #{ticket}: {open_d.price:.5f} → {close_d.price:.5f} = {pips:+.1f}p")
                return pips
            else:
                log.warning(f"Pips #{ticket}: Unrealistisch {pips}p — nutze Profit-Fallback")

        # Methode 2: Dollar-Profit direkt aus Deals (immer korrekt)
        total_profit = sum(d.profit + d.swap + d.commission for d in pos_deals)
        if abs(total_profit) > 0.001:
            log.info(f"Pips #{ticket}: Profit-Fallback ${total_profit:.2f}")
            return round(total_profit, 2)

        return 0.0

    except Exception as e:
        log.error(f"Pips #{ticket}: {e}")
        return None


def check_closed_trades(state: dict, risk: RiskManager):
    memory       = get_memory()
    agent_magics = set(MAGIC_NUMBERS.values())
    positions    = mt5.positions_get() or []
    open_tickets = {p.ticket for p in positions if p.magic in agent_magics}
    tracked: dict = state.get("open_tickets", {})
    closed_now    = [t for t in tracked if int(t) not in open_tickets]

    for ticket_str in closed_now:
        info   = tracked[ticket_str]
        ticket = int(ticket_str)
        pips   = _get_closed_pips(ticket, info)
        reason = info.get("close_reason", "unknown")

        if pips is not None:
            prev_bl = memory.is_blacklisted(info["symbol"], info["pattern"], info["timeframe"])[0]

            memory.record_trade_result(
                symbol=info["symbol"], pattern=info["pattern"],
                timeframe=info["timeframe"], pips=pips, ticket=ticket,
            )

            now_bl, bl_reason = memory.is_blacklisted(info["symbol"], info["pattern"], info["timeframe"])

            # Cooldown aufzeichnen
            risk.record_close(info["symbol"])

            # Telegram: Close
            if NOTIFY_TRADE_CLOSE:
                profit_approx = pips * pip_value(info["symbol"]) * info.get("lot", 0.01) * 100000
                tg.notify_trade_closed(
                    ticket=ticket, symbol=info["symbol"],
                    direction=info.get("direction", "?"),
                    pips=pips, profit=profit_approx, reason=reason,
                )

            # Telegram: Blacklist
            if NOTIFY_BLACKLIST and now_bl and not prev_bl:
                tg.notify_blacklist(info["symbol"], info["pattern"], info["timeframe"], bl_reason)

            log.info(f"Trade #{ticket} geschlossen: {info['symbol']} {'+' if pips>=0 else ''}{pips:.1f}p")

            # Journal: Close aufzeichnen
            try:
                from trade_journal import record_trade_close
                profit_approx = pips * pip_value(info["symbol"]) * info.get("lot", 0.01) * 100000
                record_trade_close(
                    ticket=ticket, close_price=0, pips=pips,
                    profit_usd=profit_approx, close_reasoning=reason,
                )
            except Exception as e:
                log.debug(f"Journal close: {e}")

        del tracked[ticket_str]

    state["open_tickets"] = tracked


def track_new_trade(state: dict, ticket: int, setup: Setup, lot: float, direction: str):
    state.setdefault("open_tickets", {})[str(ticket)] = {
        "symbol":    setup.symbol,
        "pattern":   setup.pattern,
        "timeframe": setup.timeframe,
        "entry":     setup.entry_price,
        "direction": direction,
        "lot":       lot,
        "opened_at": datetime.utcnow().isoformat(),
    }


# -----------------------------------------------------------------------------
# Setup verarbeiten
# -----------------------------------------------------------------------------

def process_setup(setup: Setup, risk: RiskManager, state: dict) -> bool:
    memory = get_memory()

    # Blacklist
    blacklisted, bl_reason = memory.is_blacklisted(setup.symbol, setup.pattern, setup.timeframe)
    if blacklisted:
        log.debug(f"Blacklist: {setup.symbol} {setup.pattern} {setup.timeframe}")
        return False

    # Risk-Check 1
    allowed, reason = risk.can_open_trade(setup.symbol)
    if not allowed:
        log.info(f"Risk-Block: {setup.symbol} | {reason}")
        if "DD" in reason and NOTIFY_DD_WARNING:
            dd = risk.daily_dd_pct()
            tg.notify_daily_dd_warning(dd, DAILY_DD_LIMIT_PCT)
        return False

    # Richtungs-Diversifikation: Max 3 Trades in gleicher Richtung
    MAX_SAME_DIRECTION = 3
    positions = mt5.positions_get() or []
    agent_magics = set(MAGIC_NUMBERS.values())
    agent_pos = [p for p in positions if p.magic in agent_magics]
    buy_count  = sum(1 for p in agent_pos if p.type == mt5.POSITION_TYPE_BUY)
    sell_count = sum(1 for p in agent_pos if p.type == mt5.POSITION_TYPE_SELL)
    if setup.direction == "BUY" and buy_count >= MAX_SAME_DIRECTION:
        log.info(f"Risk-Block: {setup.symbol} | Max {MAX_SAME_DIRECTION} BUY Trades erreicht ({buy_count})")
        return False
    if setup.direction == "SELL" and sell_count >= MAX_SAME_DIRECTION:
        log.info(f"Risk-Block: {setup.symbol} | Max {MAX_SAME_DIRECTION} SELL Trades erreicht ({sell_count})")
        return False

    # Memory-Kontext
    memory_context = memory.get_context_for_claude(setup.symbol, setup.pattern, setup.timeframe)

    # Chart-Bilder generieren für Claude Vision
    sig_str = ",".join(setup.signals) if hasattr(setup, "signals") else "?"
    log.info(f"Analysiere: {setup.symbol} {setup.pattern} {setup.direction} Conf:{getattr(setup, 'confluence', '?')} D1:{getattr(setup, 'd1_bias', '?')} Zone:{getattr(setup, 'h4_at_zone', '?')} [{sig_str}]")

    chart_paths = {}
    try:
        from chart_renderer import render_multi_tf_charts
        chart_data = getattr(setup, "chart_data", {})
        if chart_data:
            chart_paths = render_multi_tf_charts(setup.symbol, chart_data)
            if chart_paths:
                log.info(f"  Charts: {', '.join(chart_paths.keys())}")
    except Exception as e:
        log.warning(f"Chart-Rendering: {e} — Text-only Analyse")

    # Claude Vision Analyse (mit Charts wenn verfügbar)
    try:
        from analyzer import analyze_setup_with_charts
        analysis = analyze_setup_with_charts(setup, chart_paths=chart_paths, memory_context=memory_context)
    except ImportError:
        analysis = analyze_setup(setup, memory_context=memory_context)
    if analysis is None:
        return False

    if analysis.quality not in MIN_QUALITY:
        log.info(f"Qualität {analysis.quality} → SKIP")
        try:
            from trade_journal import record_skip
            record_skip(setup.symbol, f"Quality {analysis.quality}: {analysis.reasoning[:150]}", analysis.quality)
        except Exception:
            pass
        return False

    if analysis.decision != "TRADE":
        log.info(f"Claude SKIP: {setup.symbol} {setup.pattern} | {analysis.reasoning[:60]}")
        try:
            from trade_journal import record_skip
            record_skip(setup.symbol, f"SKIP: {analysis.reasoning[:150]}", analysis.quality)
        except Exception:
            pass
        return False

    # === v8: Python berechnet SL/TP (nicht Claude!) ===
    from scanner import pip_value, fetch_candles
    pip = pip_value(setup.symbol)
    
    # SL/TP aus Scanner-Daten (ATR + Swing-basiert)
    # Claude hat nur Richtung bestätigt — SL/TP kommen vom Scanner
    sl_pips = setup.sl_pips
    tp_pips = setup.tp_pips
    
    # Richtung von Claude übernehmen (Scanner könnte falsch sein)
    direction = analysis.direction
    
    # R:R Check (dynamisch nach Quality)
    min_rr = 1.5 if analysis.quality == "A" else 2.0
    actual_rr = tp_pips / sl_pips if sl_pips > 0 else 0
    if actual_rr < min_rr:
        # Versuche TP zu erweitern
        tp_pips = sl_pips * min_rr
        actual_rr = min_rr
        log.info(f"  R:R angepasst: TP {tp_pips:.1f}p für R:R {min_rr}")
    
    # Speichere in analysis für downstream
    analysis.sl_pips = sl_pips
    analysis.tp_pips = tp_pips
    
    # Risk-Check 2 (nach Claude)
    allowed, reason = risk.can_open_trade(setup.symbol)
    if not allowed:
        return False

    # Lot berechnen — v8: Quality A = volles Risiko, B = halbes
    quality_lot_factor = 1.0 if analysis.quality == "A" else 0.5
    mem_lot_factor = memory.get_lot_factor(setup.symbol, setup.pattern, setup.timeframe)
    final_lot = calculate_lot(
        setup.symbol, setup.pattern, setup.timeframe,
        mem_lot_factor * quality_lot_factor, sl_pips=sl_pips,
    )

    # Dollar-Risk Validierung
    ok, risk_reason = risk.validate_trade_risk(setup.symbol, final_lot, analysis.sl_pips)
    if not ok:
        log.warning(f"Risk-Reject: {setup.symbol} {risk_reason}")
        return False

    # === v8: Spread-Check vor Order (alle 4 AIs: MUSS REIN) ===
    tick = mt5.symbol_info_tick(setup.symbol)
    if tick:
        current_spread = tick.ask - tick.bid
        pip = pip_value(setup.symbol)
        spread_pips = current_spread / pip if pip > 0 else 999
        sl_pips_val = analysis.sl_pips if analysis.sl_pips > 0 else 20

        # Spread darf max 20% des SL fressen
        if spread_pips > sl_pips_val * 0.20:
            log.warning(f"Spread-Block: {setup.symbol} Spread {spread_pips:.1f}p > 20% von SL {sl_pips_val:.1f}p")
            return False

        # Absoluter Spread-Check: nicht mehr als 5 Pips für Forex, 40 für Gold
        max_spread = 40.0 if "XAU" in setup.symbol or "XAG" in setup.symbol or "XTI" in setup.symbol else 5.0
        if spread_pips > max_spread:
            log.warning(f"Spread-Block: {setup.symbol} Spread {spread_pips:.1f}p > Max {max_spread}")
            return False

    # === v8: Session-Sperre Rollover (21:30-00:30 UTC) ===
    import datetime as _dt
    utc_hour = _dt.datetime.utcnow().hour
    utc_min = _dt.datetime.utcnow().minute
    utc_time_min = utc_hour * 60 + utc_min  # Minuten seit Mitternacht
    # 21:30 = 1290, 00:30 = 30 → Sperre wenn > 1290 ODER < 30
    if utc_time_min >= 1290 or utc_time_min <= 30:
        log.info(f"Session-Block: Rollover-Fenster {utc_hour:02d}:{utc_min:02d} UTC — kein neuer Trade")
        return False

    # Trade öffnen
    ticket = open_trade(setup, analysis, lot_override=final_lot)
    if not ticket:
        return False

    # Tracking
    track_new_trade(state, ticket, setup, final_lot, analysis.direction)

    # Telegram: Open
    if NOTIFY_TRADE_OPEN:
        tick = mt5.symbol_info_tick(setup.symbol)
        pip = pip_value(setup.symbol)
        if tick:
            entry = tick.ask if analysis.direction == "BUY" else tick.bid
        else:
            entry = setup.entry_price

        sl = entry - analysis.sl_pips * pip if analysis.direction == "BUY" else entry + analysis.sl_pips * pip
        tp = entry + analysis.tp_pips * pip if analysis.direction == "BUY" else entry - analysis.tp_pips * pip
        rr = analysis.tp_pips / analysis.sl_pips if analysis.sl_pips > 0 else 0

        tg.notify_trade_opened(
            ticket=ticket, symbol=setup.symbol, direction=analysis.direction,
            lot=final_lot, entry=round(entry, 5), sl=round(sl, 5), tp=round(tp, 5),
            sl_pips=analysis.sl_pips, tp_pips=analysis.tp_pips, rr=rr,
            pattern=setup.pattern, timeframe=setup.timeframe,
            quality=analysis.quality, confidence=analysis.confidence,
            reasoning=analysis.reasoning,
        )

    log.info(
        f"Trade #{ticket} | {setup.symbol} {analysis.direction} | "
        f"Lot:{final_lot} | Q:{analysis.quality} C:{analysis.confidence}%"
    )

    # Journal: Trade aufzeichnen
    try:
        from trade_journal import record_trade_open
        tick = mt5.symbol_info_tick(setup.symbol)
        pip = pip_value(setup.symbol)
        entry = tick.ask if tick and analysis.direction == "BUY" else (tick.bid if tick else setup.entry_price)
        sl = entry - analysis.sl_pips * pip if analysis.direction == "BUY" else entry + analysis.sl_pips * pip
        tp = entry + analysis.tp_pips * pip if analysis.direction == "BUY" else entry - analysis.tp_pips * pip
        record_trade_open(
            ticket=ticket, symbol=setup.symbol, direction=analysis.direction,
            lot=final_lot, entry_price=entry, sl_price=sl, tp_price=tp,
            quality=analysis.quality, confidence=analysis.confidence,
            reasoning=analysis.reasoning,
        )
    except Exception as e:
        log.debug(f"Journal record: {e}")

    return True


# -----------------------------------------------------------------------------
# Zeitgesteuerte Tasks
# -----------------------------------------------------------------------------

def _should_run_meta_analysis(state: dict) -> bool:
    now = datetime.utcnow()
    if now.hour != META_ANALYSIS_HOUR_UTC:
        return False
    today = now.strftime("%Y-%m-%d")
    return state.get("last_meta_date", "") != today


def _should_run_daily_summary(state: dict) -> bool:
    now   = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    return (
        NOTIFY_DAILY_SUMMARY
        and now.hour == META_ANALYSIS_HOUR_UTC
        and state.get("last_summary_date", "") != today
    )


# -----------------------------------------------------------------------------
# Haupt-Loop
# -----------------------------------------------------------------------------

def run_agent():
    log.info("=" * 65)
    log.info("  AI TRADING AGENT v4")
    if DEMO_MODE:
        log.info("  *** DEMO-MODUS ***")
    log.info(f"  Account: #{EXPECTED_ACCOUNT}")
    log.info(f"  Scan: {SCAN_INTERVAL_SEC//60}min | Manage: {MANAGE_INTERVAL_SEC//60}min")
    log.info(f"  Max {MAX_OPEN_TRADES} Trades | DD {DAILY_DD_LIMIT_PCT}% | Meta {META_ANALYSIS_HOUR_UTC}:00 UTC")
    log.info("=" * 65)

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY nicht gesetzt!")
        sys.exit(1)

    tg.init(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

    if not connect_mt5():
        sys.exit(1)

    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    memory = get_memory()
    risk   = RiskManager()
    state  = load_state()

    now_time = time.time()
    last_scan_time   = 0.0
    last_manage_time = 0.0
    last_meta_check  = now_time   # Nicht bei 0 starten

    total_scan_count  = 0
    total_trade_count = 0

    log.info("Agent läuft. Strg+C zum Beenden.")
    memory.print_full_report()

    while _running:
        now = time.time()

        # Geschlossene Trades → Memory + Telegram
        try:
            check_closed_trades(state, risk)
        except Exception as e:
            log.error(f"check_closed_trades: {e}", exc_info=True)

        # Trade-Management (alle 5 Min)
        if now - last_manage_time >= MANAGE_INTERVAL_SEC:
            try:
                stats = manage_all_trades()
                if NOTIFY_BREAKEVEN and stats.get("be_set", 0) > 0:
                    pass   # BE Details optional erweiterbar
                risk.log_status()
                last_manage_time = now
                state["last_manage"] = datetime.utcnow().isoformat()
                save_state(state)
            except Exception as e:
                log.error(f"Manage: {e}", exc_info=True)

        # Zeitgesteuerte Tasks (stündlich prüfen)
        if now - last_meta_check >= 3600:
            last_meta_check = now

            if _should_run_daily_summary(state):
                try:
                    send_daily_summary()
                    state["last_summary_date"] = datetime.utcnow().strftime("%Y-%m-%d")
                    save_state(state)
                except Exception as e:
                    log.error(f"Daily Summary: {e}", exc_info=True)

            if _should_run_meta_analysis(state):
                log.info("Starte Meta-Analyse...")
                try:
                    run_meta_analysis(send_telegram=NOTIFY_META_ANALYSIS)
                    state["last_meta_date"] = datetime.utcnow().strftime("%Y-%m-%d")
                    save_state(state)
                except Exception as e:
                    log.error(f"Meta-Analyse: {e}", exc_info=True)
                    tg.notify_error("Meta-Analyse", str(e))

                # Tägliche Selbst-Reflexion: Claude analysiert seine Trades
                log.info("Starte tägliche Selbst-Reflexion...")
                try:
                    from analyzer import run_daily_reflection
                    reflection = run_daily_reflection()
                    if reflection:
                        tg.send_raw(f"📝 <b>Tägliche Reflexion</b>\n\n{reflection[:500]}")
                except Exception as e:
                    log.error(f"Reflexion: {e}", exc_info=True)

        # Setup-Scan (alle 15 Min)
        if now - last_scan_time >= SCAN_INTERVAL_SEC:
            if not is_market_open():
                log.debug("Markt geschlossen — kein Scan (spart API-Tokens)")
                last_scan_time = now
            else:
                total_scan_count += 1
                log.info(f"--- Scan #{total_scan_count} | {datetime.utcnow().strftime('%H:%M:%S')} UTC ---")
                try:
                    if risk.is_daily_dd_exceeded():
                        log.warning("Daily DD Limit — Scan pausiert")
                    else:
                        # === STEP 1: Claude wählt Watchlist aus Overview ===
                        watchlist = None
                        try:
                            from chart_renderer import render_overview_grid
                            from analyzer import screen_overview
                            from scanner import fetch_all_d1_data, get_available_symbols

                            log.info("Step 1: Overview-Grid rendern...")
                            d1_data = fetch_all_d1_data()
                            if d1_data:
                                overview_path = render_overview_grid(d1_data)
                                if overview_path:
                                    available = list(d1_data.keys())
                                    watchlist = screen_overview(overview_path, available)
                        except Exception as e:
                            log.warning(f"Overview-Screening: {e} — scanne alle")

                        # === STEP 2: Deep Analysis nur für Watchlist ===
                        if watchlist:
                            log.info(f"Step 2: Deep Analysis für {len(watchlist)} Paare...")
                        setups = scan_all(symbol_filter=watchlist)

                        for setup in setups:
                            if not _running:
                                break
                            if process_setup(setup, risk, state):
                                total_trade_count += 1
                                time.sleep(2)

                    last_scan_time = now
                    state.update({
                        "last_scan":   datetime.utcnow().isoformat(),
                        "scan_count":  total_scan_count,
                        "trade_count": total_trade_count,
                    })
                    save_state(state)
                except Exception as e:
                    log.error(f"Scan: {e}", exc_info=True)
                    tg.notify_error("Scan-Loop", str(e))
                    last_scan_time = now

        # Warten
        next_m = last_manage_time + MANAGE_INTERVAL_SEC - time.time()
        next_s = last_scan_time   + SCAN_INTERVAL_SEC   - time.time()
        time.sleep(max(5.0, min(next_m, next_s, 30.0)))

    # Shutdown
    log.info(f"Agent beendet. Scans:{total_scan_count} Trades:{total_trade_count}")
    state["stopped_at"] = datetime.utcnow().isoformat()
    save_state(state)
    tg.notify_agent_stop(total_scan_count, total_trade_count)
    mt5.shutdown()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def emergency_close_all():
    tg.init(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    log.warning("NOTFALL: Alle Agent-Trades werden geschlossen!")
    if not connect_mt5():
        sys.exit(1)
    closed = close_all_agent_trades("emergency_cli")
    log.info(f"{closed} Positionen geschlossen")
    tg.send_raw(f"⚠️ NOTFALL-CLOSE: {closed} Positionen geschlossen")
    mt5.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Trading Agent v4")
    parser.add_argument("--close-all", action="store_true", help="Alle Agent-Positionen schließen")
    parser.add_argument("--report",    action="store_true", help="Memory-Report ausgeben")
    parser.add_argument("--meta",      action="store_true", help="Meta-Analyse jetzt ausführen")
    args = parser.parse_args()

    tg.init(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

    if args.close_all:
        emergency_close_all()
    elif args.report:
        get_memory().print_full_report()
    elif args.meta:
        if not mt5.initialize():
            log.warning("MT5 nicht verbunden — Account-Daten übersprungen")
        run_meta_analysis(send_telegram=True)
        mt5.shutdown()
    else:
        run_agent()
