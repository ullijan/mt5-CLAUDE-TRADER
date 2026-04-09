# =============================================================================
# trade_journal.py — Claudes Trading-Journal (Selbstlern-System)
# C:\mt5_agent\trade_journal.py
#
# Speichert:
#   - Jeden Trade: Entry, Exit, Reasoning, Charts-Analyse
#   - Claudes eigene Regeln (geschrieben durch tägliche Selbst-Reflexion)
#   - Lessons Learned pro Symbol/Setup
#
# Wird bei jedem Scan als Kontext mitgegeben:
#   → Claude liest seine Erfahrungen bevor er entscheidet
# =============================================================================

from __future__ import annotations
import json
import os
from datetime import datetime, timedelta
from typing import Optional

from logger_setup import get_logger

log = get_logger("journal")

JOURNAL_FILE   = r"C:\mt5_agent\trade_journal.json"
RULES_FILE     = r"C:\mt5_agent\claude_rules.json"
REFLECTION_FILE = r"C:\mt5_agent\daily_reflections.json"


# =============================================================================
# Journal: Trade-Aufzeichnungen
# =============================================================================

def _load_json(filepath: str, default=None):
    if default is None:
        default = []
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def _save_json(filepath: str, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        log.error(f"Journal save {filepath}: {e}")


def record_trade_open(
    ticket: int,
    symbol: str,
    direction: str,
    lot: float,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    quality: str,
    confidence: int,
    reasoning: str,
    watchlist_reasoning: str = "",
):
    """Speichert einen neuen Trade mit Claudes Begründung."""
    journal = _load_json(JOURNAL_FILE, [])

    entry = {
        "ticket": ticket,
        "symbol": symbol,
        "direction": direction,
        "lot": lot,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "quality": quality,
        "confidence": confidence,
        "reasoning": reasoning,
        "watchlist_reasoning": watchlist_reasoning,
        "opened_at": datetime.utcnow().isoformat(),
        "closed_at": None,
        "close_price": None,
        "pips": None,
        "profit_usd": None,
        "close_reasoning": None,
        "result": None,   # "WIN", "LOSS", "BE"
    }

    journal.append(entry)

    # Nur letzte 200 Trades behalten
    if len(journal) > 200:
        journal = journal[-200:]

    _save_json(JOURNAL_FILE, journal)
    log.info(f"Journal: Trade #{ticket} {symbol} {direction} aufgezeichnet")


def record_trade_close(
    ticket: int,
    close_price: float,
    pips: float,
    profit_usd: float,
    close_reasoning: str = "",
):
    """Aktualisiert einen Trade mit dem Ergebnis."""
    journal = _load_json(JOURNAL_FILE, [])

    for entry in reversed(journal):
        if entry["ticket"] == ticket:
            entry["closed_at"] = datetime.utcnow().isoformat()
            entry["close_price"] = close_price
            entry["pips"] = round(pips, 1)
            entry["profit_usd"] = round(profit_usd, 2)
            entry["close_reasoning"] = close_reasoning
            if pips > 1:
                entry["result"] = "WIN"
            elif pips < -1:
                entry["result"] = "LOSS"
            else:
                entry["result"] = "BE"
            break

    _save_json(JOURNAL_FILE, journal)
    log.info(f"Journal: Trade #{ticket} geschlossen: {'+' if pips >= 0 else ''}{pips:.1f}p")


def record_skip(
    symbol: str,
    reasoning: str,
    quality: str,
):
    """Zeichnet auch Skips auf — wichtig um zu lernen wann NICHT traden richtig war."""
    journal = _load_json(JOURNAL_FILE, [])

    # Nur letzte 50 Skips behalten (platzsparend)
    skips = [e for e in journal if e.get("type") == "SKIP"]
    if len(skips) > 50:
        # Älteste Skips entfernen
        journal = [e for e in journal if e.get("type") != "SKIP"] + skips[-50:]

    journal.append({
        "type": "SKIP",
        "symbol": symbol,
        "quality": quality,
        "reasoning": reasoning[:200],
        "timestamp": datetime.utcnow().isoformat(),
    })

    _save_json(JOURNAL_FILE, journal)


# =============================================================================
# Regeln: Claudes selbstgeschriebene Trading-Regeln
# =============================================================================

def get_rules() -> list[str]:
    """Gibt Claudes aktuelle Regeln zurück."""
    data = _load_json(RULES_FILE, {"rules": [], "updated": ""})
    if isinstance(data, dict):
        return data.get("rules", [])
    return []


def save_rules(rules: list[str]):
    """Speichert neue Regeln (von Claudes täglicher Selbst-Reflexion)."""
    _save_json(RULES_FILE, {
        "rules": rules[-30:],   # Max 30 Regeln
        "updated": datetime.utcnow().isoformat(),
    })
    log.info(f"Regeln aktualisiert: {len(rules)} Regeln")


# =============================================================================
# Kontext für Claude: Erfahrungen + Regeln als Prompt-Text
# =============================================================================

def get_experience_context(symbol: str = "", max_trades: int = 15) -> str:
    """
    Baut den Erfahrungs-Kontext für Claudes Prompt.
    Enthält: Letzte Trades, Symbol-spezifische Erfahrungen, Regeln.
    """
    journal = _load_json(JOURNAL_FILE, [])
    rules = get_rules()

    lines = []

    # === Claudes eigene Regeln ===
    if rules:
        lines.append("DEINE EIGENEN REGELN (du hast diese selbst geschrieben):")
        for i, rule in enumerate(rules, 1):
            lines.append(f"  {i}. {rule}")
        lines.append("")

    # === Symbol-spezifische Erfahrungen ===
    if symbol:
        sym_trades = [t for t in journal if t.get("symbol") == symbol and t.get("result")]
        if sym_trades:
            wins = sum(1 for t in sym_trades if t["result"] == "WIN")
            losses = sum(1 for t in sym_trades if t["result"] == "LOSS")
            total_pips = sum(t.get("pips", 0) for t in sym_trades if t.get("pips"))

            lines.append(f"DEINE ERFAHRUNG MIT {symbol}:")
            lines.append(f"  {len(sym_trades)} Trades | {wins}W {losses}L | {total_pips:+.1f} Pips")

            # Letzte 3 Trades für dieses Symbol
            for t in sym_trades[-3:]:
                sign = "+" if (t.get("pips", 0) or 0) >= 0 else ""
                lines.append(
                    f"  {t.get('opened_at', '?')[:10]} {t['direction']} {sign}{t.get('pips', 0)}p | "
                    f"Dein Reasoning: \"{t.get('reasoning', '')[:100]}\""
                )
                if t["result"] == "LOSS" and t.get("close_reasoning"):
                    lines.append(f"    → Geschlossen weil: \"{t['close_reasoning'][:80]}\"")
            lines.append("")

    # === Letzte Trades (alle Symbole) ===
    closed_trades = [t for t in journal if t.get("result") and t.get("type") != "SKIP"]
    if closed_trades:
        recent = closed_trades[-max_trades:]
        wins = sum(1 for t in recent if t["result"] == "WIN")
        losses = sum(1 for t in recent if t["result"] == "LOSS")
        total_pips = sum(t.get("pips", 0) for t in recent if t.get("pips"))

        lines.append(f"LETZTE {len(recent)} TRADES: {wins}W {losses}L | {total_pips:+.1f} Pips")
        for t in recent[-5:]:
            sign = "+" if (t.get("pips", 0) or 0) >= 0 else ""
            lines.append(
                f"  {t.get('opened_at', '?')[:10]} {t['symbol']} {t['direction']} "
                f"{sign}{t.get('pips', 0)}p Q:{t.get('quality', '?')}"
            )
        lines.append("")

    if not lines:
        lines.append("ERFAHRUNGEN: Noch keine Trades aufgezeichnet. Sei vorsichtig am Anfang.")

    return "\n".join(lines)


# =============================================================================
# Tägliche Selbst-Reflexion (22:00 UTC)
# =============================================================================

def get_daily_trades() -> list[dict]:
    """Gibt alle Trades von heute zurück."""
    journal = _load_json(JOURNAL_FILE, [])
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return [t for t in journal
            if t.get("opened_at", "").startswith(today) and t.get("type") != "SKIP"]


def get_daily_skips() -> list[dict]:
    """Gibt alle Skips von heute zurück."""
    journal = _load_json(JOURNAL_FILE, [])
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return [t for t in journal
            if t.get("type") == "SKIP" and t.get("timestamp", "").startswith(today)]


def get_reflection_prompt() -> str:
    """Baut den Prompt für die tägliche Selbst-Reflexion (Trades + Skips)."""
    today_trades = get_daily_trades()
    today_skips = get_daily_skips()
    rules = get_rules()

    if not today_trades and not today_skips:
        return ""

    lines = []

    # === Trades ===
    if today_trades:
        lines.append(f"DEINE TRADES HEUTE ({len(today_trades)}):")
        for t in today_trades:
            result = t.get("result", "OFFEN")
            pips = t.get("pips", "?")
            lines.append(
                f"  {t.get('symbol', '?')} {t.get('direction', '?')} | "
                f"Ergebnis: {result} {pips}p | Q:{t.get('quality', '?')} C:{t.get('confidence', '?')}%"
            )
            lines.append(f"    Entry-Reasoning: \"{t.get('reasoning', '')[:150]}\"")
            if t.get("close_reasoning"):
                lines.append(f"    Close-Reasoning: \"{t['close_reasoning'][:100]}\"")

    # === Skips (maximal 10 zeigen) ===
    if today_skips:
        lines.append(f"\nDEINE SKIPS HEUTE ({len(today_skips)} — du hast NICHT getradet):")
        for s in today_skips[-10:]:
            lines.append(
                f"  {s.get('symbol', '?')} | Q:{s.get('quality', '?')} | "
                f"Reason: \"{s.get('reasoning', '')[:120]}\""
            )
        lines.append("  → Waren diese Skips RICHTIG? Hättest du einen davon doch traden sollen?")

    # === Aktuelle Regeln ===
    if rules:
        lines.append(f"\nDEINE AKTUELLEN REGELN ({len(rules)}):")
        for r in rules:
            lines.append(f"  • {r}")

    lines.append("\nANALYSIERE:")
    lines.append("1. Was lief heute gut? Warum?")
    lines.append("2. Was lief schlecht? Was hättest du anders machen sollen?")
    lines.append("3. Waren deine Skips richtig? Gab es verpasste Chancen?")
    lines.append("4. Schreibe deine aktualisierten Regeln (alte behalten, neue hinzufügen, schlechte entfernen)")
    lines.append("")
    lines.append('ANTWORT ALS JSON: {"analysis": "...", "new_rules": ["Regel 1", "Regel 2", ...]}')

    return "\n".join(lines)


def save_reflection(analysis: str, new_rules: list[str]):
    """Speichert die tägliche Reflexion."""
    reflections = _load_json(REFLECTION_FILE, [])
    reflections.append({
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "analysis": analysis[:1000],
        "rules_count": len(new_rules),
    })
    # Nur letzte 30 Reflexionen
    reflections = reflections[-30:]
    _save_json(REFLECTION_FILE, reflections)

    # Regeln aktualisieren
    save_rules(new_rules)
    log.info(f"Tägliche Reflexion gespeichert: {len(new_rules)} Regeln")
