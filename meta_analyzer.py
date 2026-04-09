# =============================================================================
# meta_analyzer.py — Strategische KI-Gesamtanalyse (v4)
# C:\mt5_agent\meta_analyzer.py
#
# v4 Fixes:
#   - CLAUDE_META_MODEL (separates Modell für Meta-Analyse)
#   - History-Truncation auf 4000 statt 2000 Zeichen
#   - MEMORY_FILE / TRADE_LOG aus config importiert
# =============================================================================

from __future__ import annotations
import json
import csv
import os
from datetime import datetime, timedelta
from typing import Optional
import MetaTrader5 as mt5
import anthropic
import time

from config import (
    CLAUDE_META_MODEL, ANTHROPIC_API_KEY, CLAUDE_META_MAX_TOKENS,
    MAGIC_NUMBERS, TRADE_LOG, BASE_DIR,
)
from memory import get_memory
from logger_setup import get_logger

log = get_logger("meta_analyzer")


def _api_call_with_retry(client, max_retries=3, base_delay=30, **kwargs):
    """Wrapper mit Retry bei API-Fehlern."""
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except (anthropic.OverloadedError, anthropic.RateLimitError,
                anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                log.warning(f"API {type(e).__name__}, Retry {attempt+1}/{max_retries} in {delay}s...")
                time.sleep(delay)
            else:
                raise

META_REPORT_FILE  = os.path.join(BASE_DIR, "meta_report.md")
META_HISTORY_FILE = os.path.join(BASE_DIR, "meta_history.json")


# =============================================================================
# Daten-Sammlung
# =============================================================================

def _collect_memory_stats() -> str:
    memory = get_memory()
    lines = ["=== PERFORMANCE PER KOMBINATION ==="]

    if not memory.combo_stats:
        return "Noch keine Trade-Daten."

    sorted_combos = sorted(
        memory.combo_stats.values(),
        key=lambda c: c.expectancy_pips,
        reverse=True,
    )

    for c in sorted_combos:
        if c.trades == 0:
            continue
        status = "BLACKLIST" if c.blacklisted else "aktiv"
        lines.append(
            f"{c.symbol} | {c.pattern} | {c.timeframe}: "
            f"{c.trades}T | WR {c.win_rate*100:.0f}% | "
            f"Avg+ {c.avg_win_pips:.1f}p Avg- {c.avg_loss_pips:.1f}p | "
            f"Expect {c.expectancy_pips:.1f}p | Streak {c.streak:+d} | "
            f"Lot {c.lot_factor:.2f} | {status}"
        )
        if c.notes:
            lines.append(f"  Notiz: {c.notes[-1]}")

    lines.append("\n=== SYMBOL-ÜBERSICHT ===")
    for s in sorted(memory.symbol_stats.values(), key=lambda x: x.total_pips, reverse=True):
        if s.trades > 0:
            lines.append(
                f"{s.symbol}: {s.trades}T | WR {s.win_rate*100:.0f}% | "
                f"Total {s.total_pips:+.0f}p | Lot {s.lot_factor:.2f}"
            )

    return "\n".join(lines)


def _collect_recent_trades(days: int = 14) -> str:
    if not os.path.exists(TRADE_LOG):
        return "Keine Trade-History."

    cutoff = datetime.utcnow() - timedelta(days=days)
    lines  = [f"=== TRADE-HISTORY (letzte {days} Tage) ==="]

    try:
        with open(TRADE_LOG, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        recent = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row.get("timestamp", ""))
                if ts >= cutoff:
                    recent.append(row)
            except:
                continue

        if not recent:
            return f"Keine Trades in den letzten {days} Tagen."

        lines.append(f"Anzahl: {len(recent)}")

        # Letzte 10
        lines.append("\nLetzte 10:")
        for r in recent[-10:]:
            lines.append(
                f"  {r.get('timestamp','')[:16]} | {r.get('symbol','')} {r.get('direction','')} | "
                f"{r.get('pattern','')} {r.get('timeframe','')} | "
                f"Q:{r.get('quality','')} C:{r.get('confidence','')}% | "
                f"SL:{r.get('sl_pips','')}p TP:{r.get('tp_pips','')}p R:R {r.get('rr','')}"
            )

    except Exception as e:
        return f"Trade-Log Fehler: {e}"

    return "\n".join(lines)


def _collect_open_positions() -> str:
    positions    = mt5.positions_get() or []
    agent_magics = set(MAGIC_NUMBERS.values())
    agent_pos    = [p for p in positions if p.magic in agent_magics]

    if not agent_pos:
        return "=== OFFENE POSITIONEN: Keine ==="

    lines = [f"=== OFFENE POSITIONEN ({len(agent_pos)}) ==="]
    for p in agent_pos:
        direction = "BUY" if p.type == 0 else "SELL"
        lines.append(
            f"#{p.ticket} {p.symbol} {direction} | "
            f"Entry:{p.price_open} SL:{p.sl} TP:{p.tp} | "
            f"Profit:{p.profit:+.2f}"
        )
    return "\n".join(lines)


def _collect_account_info() -> str:
    acc = mt5.account_info()
    if not acc:
        return "Account-Info nicht verfügbar"
    return (
        f"=== ACCOUNT ===\n"
        f"#{acc.login} | {acc.server}\n"
        f"Balance: {acc.balance:.2f} {acc.currency} | Equity: {acc.equity:.2f} | "
        f"Margin: {acc.margin:.2f} | Free: {acc.margin_free:.2f} | "
        f"Leverage: 1:{acc.leverage}"
    )


def _load_previous_recommendations() -> str:
    if not os.path.exists(META_HISTORY_FILE):
        return "Keine vorherige Analyse."
    try:
        with open(META_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        last = history[-1] if history else {}
        return f"=== LETZTE ANALYSE ({last.get('timestamp', '?')}) ===\n{last.get('short_summary', '')[:800]}"
    except:
        return "Vorherige Analyse nicht lesbar."


# =============================================================================
# Claude Prompt
# =============================================================================

META_SYSTEM_PROMPT = """Du bist ein quantitativer Trading-Stratege der ein autonomes AI-Trading-System analysiert.

Das System handelt Price Action (Inside Bar, Engulfing, Breakout, S/R Bounce) auf:
- EURUSD, GBPUSD, XAUUSD (und optional DAX)
- Timeframes: M15, H1, H4
- Via MetaTrader 5, Claude API für Entry-Entscheidungen

ANTWORTE IN ZWEI TEILEN:

TEIL 1 — KURZ-ZUSAMMENFASSUNG (max 5 Sätze):
Was läuft gut, was schlecht, wichtigste Aktion.

TEIL 2 — EMPFEHLUNGEN (nach Priorität: SOFORT / DIESE WOCHE / LANGFRISTIG):
- Welche Kombinationen stoppen oder stärken
- Lot-Anpassungen, Parameter-Änderungen
- Risiko-Anpassungen
- Verbesserungen an Entry-Qualität

Sei direkt und konkret."""


# =============================================================================
# Haupt-Funktion
# =============================================================================

def run_meta_analysis(send_telegram: bool = True) -> Optional[str]:
    log.info("Meta-Analyse startet...")

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY nicht gesetzt")
        return None

    sections = [
        _collect_account_info(),
        _collect_open_positions(),
        _collect_memory_stats(),
        _collect_recent_trades(14),
        _load_previous_recommendations(),
    ]

    full_data = "\n\n".join(sections)
    ts_str    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    user_prompt = f"""Analysiere das AI-Trading-System und gib Handlungsempfehlungen.

SYSTEM-DATEN ({ts_str}):

{full_data}

Analyse und Empfehlungen:"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = _api_call_with_retry(
            client,
            model=CLAUDE_META_MODEL,
            max_tokens=CLAUDE_META_MAX_TOKENS,
            system=META_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        full_response = response.content[0].text.strip()
        log.info(f"Meta-Analyse: {len(full_response)} Zeichen")

    except Exception as e:
        log.error(f"Meta-Analyse Claude-Fehler: {e}", exc_info=True)
        return None

    # Kurz-Zusammenfassung extrahieren
    lines = full_response.split("\n")
    short_lines = []
    for line in lines:
        if line.strip().startswith("TEIL 2") or line.strip().startswith("##"):
            break
        if line.strip():
            short_lines.append(line.strip())
        if len(short_lines) >= 6:
            break
    short_summary = " ".join(short_lines)[:800]

    # Report speichern
    try:
        report_md = (
            f"# AI Trading Agent — Meta-Analyse\n"
            f"**{ts_str}**\n\n"
            f"## Zusammenfassung\n{short_summary}\n\n"
            f"## Analyse\n{full_response}\n\n"
            f"---\n## Daten-Snapshot\n```\n{full_data[:4000]}\n```\n"
        )
        with open(META_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(report_md)
    except Exception as e:
        log.error(f"Report-Save: {e}")

    # History (letzte 30)
    try:
        history = []
        if os.path.exists(META_HISTORY_FILE):
            with open(META_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        history.append({
            "timestamp":     ts_str,
            "short_summary": short_summary,
            "full_response": full_response[:4000],   # Fix: 4000 statt 2000
        })
        history = history[-30:]
        with open(META_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error(f"Meta-History: {e}")

    # Telegram
    if send_telegram:
        try:
            from telegram_notify import notify_meta_analysis
            notify_meta_analysis(full_response, short_summary)
        except Exception as e:
            log.warning(f"Telegram Meta: {e}")

    return full_response


# =============================================================================
# Daily Summary
# =============================================================================

def send_daily_summary():
    try:
        from telegram_notify import notify_daily_summary
        from config import DAILY_DD_LIMIT_PCT

        memory  = get_memory()
        acc     = mt5.account_info()
        balance = acc.balance if acc else 0
        equity  = acc.equity  if acc else 0

        dd_pct = max(0.0, (balance - equity) / balance * 100) if balance > 0 else 0.0

        positions    = mt5.positions_get() or []
        agent_magics = set(MAGIC_NUMBERS.values())
        open_count   = sum(1 for p in positions if p.magic in agent_magics)

        trades_today = 0
        pips_today   = 0.0
        if os.path.exists(TRADE_LOG):
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            try:
                with open(TRADE_LOG, "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("timestamp", "").startswith(today_str):
                            trades_today += 1
            except:
                pass

        top_lines = []
        if memory.combo_stats:
            sorted_c = sorted(
                [c for c in memory.combo_stats.values() if c.trades >= 3],
                key=lambda c: c.expectancy_pips,
                reverse=True,
            )
            for c in sorted_c[:4]:
                bl = " ⛔" if c.blacklisted else ""
                top_lines.append(
                    f"{c.symbol} {c.pattern} {c.timeframe}: "
                    f"WR {c.win_rate*100:.0f}% Expect {c.expectancy_pips:+.1f}p{bl}"
                )

        notify_daily_summary(
            balance=balance, equity=equity, dd_pct=dd_pct,
            trades_today=trades_today, pips_today=pips_today,
            open_positions=open_count, top_lines=top_lines,
        )
        log.info("Daily Summary gesendet")

    except Exception as e:
        log.error(f"Daily Summary: {e}", exc_info=True)


# =============================================================================
# Direkt ausführbar
# =============================================================================

if __name__ == "__main__":
    import sys
    if not mt5.initialize():
        print("MT5 nicht verbunden")
    result = run_meta_analysis(send_telegram=True)
    if result:
        print("\n" + "=" * 60)
        print(result)
    else:
        print("Meta-Analyse fehlgeschlagen")
        sys.exit(1)
    mt5.shutdown()
