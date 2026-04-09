# =============================================================================
# telegram_notify.py — Telegram Benachrichtigungen (v4)
# C:\mt5_agent\telegram_notify.py
#
# v4 Fixes:
#   - Message-Truncation auf 4096 Zeichen (Telegram-Limit)
#   - Retry bei Timeout/Rate-Limit (max 2 Versuche)
# =============================================================================

from __future__ import annotations
import time
import requests
import html
from datetime import datetime
from typing import Optional

from logger_setup import get_logger

log = get_logger("telegram")

_TOKEN:   str  = ""
_CHAT_ID: str  = ""
_ENABLED: bool = False

TELEGRAM_MAX_LENGTH = 4096
MAX_RETRIES = 2
RETRY_DELAY = 5   # Sekunden


def init(token: str, chat_id: str):
    global _TOKEN, _CHAT_ID, _ENABLED
    _TOKEN   = token.strip() if token else ""
    _CHAT_ID = str(chat_id).strip() if chat_id else ""
    _ENABLED = bool(_TOKEN and _CHAT_ID)
    if _ENABLED:
        log.info(f"Telegram aktiv | Chat-ID: {_CHAT_ID}")
    else:
        log.info("Telegram deaktiviert (kein Token/Chat-ID)")


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Sendet eine Nachricht mit Retry. Truncated auf 4096 Zeichen."""
    if not _ENABLED:
        return False

    # Truncation
    if len(text) > TELEGRAM_MAX_LENGTH:
        text = text[:TELEGRAM_MAX_LENGTH - 20] + "\n\n... (gekürzt)"

    url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
    payload = {
        "chat_id":    _CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                return True

            # Rate Limit → warten
            if resp.status_code == 429:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 30))
                log.warning(f"Telegram Rate Limit — {retry_after}s warten")
                time.sleep(retry_after)
                continue

            log.warning(f"Telegram {resp.status_code}: {resp.text[:100]} (Versuch {attempt})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except requests.Timeout:
            log.warning(f"Telegram Timeout (Versuch {attempt})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except Exception as e:
            log.warning(f"Telegram Fehler: {e}")
            return False

    return False


def _ts() -> str:
    return datetime.utcnow().strftime("%H:%M UTC")


# =============================================================================
# Benachrichtigungen
# =============================================================================

def notify_agent_start(balance: float, currency: str, account: int):
    _send(
        f"🟢 <b>Agent gestartet</b>\n"
        f"Account: <code>#{account}</code>\n"
        f"Balance: <b>{balance:.2f} {currency}</b>\n"
        f"Zeit: {_ts()}"
    )


def notify_agent_stop(scan_count: int, trade_count: int):
    _send(
        f"⛔ <b>Agent gestoppt</b>\n"
        f"Scans: {scan_count} | Trades: {trade_count}\n"
        f"Zeit: {_ts()}"
    )


def notify_trade_opened(
    ticket: int, symbol: str, direction: str,
    lot: float, entry: float, sl: float, tp: float,
    sl_pips: float, tp_pips: float, rr: float,
    pattern: str, timeframe: str, quality: str, confidence: int,
    reasoning: str,
):
    emoji = "🟢" if direction == "BUY" else "🔴"
    # Reasoning kürzen für Telegram
    short_reason = html.escape(reasoning[:120])
    _send(
        f"{emoji} <b>Trade #{ticket}</b>\n"
        f"<b>{symbol}</b> {direction} | {lot} Lot\n"
        f"Entry: <code>{entry}</code>\n"
        f"SL: <code>{sl}</code> ({sl_pips:.0f}p) | TP: <code>{tp}</code> ({tp_pips:.0f}p)\n"
        f"R:R {rr:.1f} | {pattern} {timeframe} | Q:<b>{quality}</b> ({confidence}%)\n"
        f"<i>{short_reason}</i>\n"
        f"⏰ {_ts()}"
    )


def notify_trade_closed(
    ticket: int, symbol: str, direction: str,
    pips: float, profit: float, reason: str,
):
    emoji = "✅" if pips >= 0 else "❌"
    sign  = "+" if pips >= 0 else ""
    _send(
        f"{emoji} <b>Trade geschlossen #{ticket}</b>\n"
        f"<b>{symbol}</b> {direction}\n"
        f"Ergebnis: <b>{sign}{pips:.1f} Pips</b> | {sign}{profit:.2f}$\n"
        f"Grund: {html.escape(reason)}\n"
        f"⏰ {_ts()}"
    )


def notify_breakeven_set(ticket: int, symbol: str, new_sl: float, progress_pct: float):
    _send(
        f"📌 <b>BE gesetzt #{ticket}</b>\n"
        f"{symbol} | SL: <code>{new_sl}</code>\n"
        f"Fortschritt: {progress_pct*100:.0f}% zum TP\n"
        f"⏰ {_ts()}"
    )


def notify_blacklist(symbol: str, pattern: str, timeframe: str, reason: str):
    _send(
        f"⛔ <b>Combo gesperrt!</b>\n"
        f"<b>{symbol}</b> | {pattern} | {timeframe}\n"
        f"Grund: {html.escape(reason[:200])}\n"
        f"⏰ {_ts()}"
    )


def notify_blacklist_lifted(symbol: str, pattern: str, timeframe: str, win_rate: float):
    _send(
        f"✅ <b>Blacklist aufgehoben</b>\n"
        f"{symbol} | {pattern} | {timeframe}\n"
        f"WR erholt: {win_rate*100:.0f}%\n"
        f"⏰ {_ts()}"
    )


def notify_daily_dd_warning(dd_pct: float, limit_pct: float):
    _send(
        f"⚠️ <b>Daily DD Limit!</b>\n"
        f"DD: <b>{dd_pct:.2f}%</b> / Limit: {limit_pct:.1f}%\n"
        f"Keine neuen Trades bis morgen\n"
        f"⏰ {_ts()}"
    )


def notify_daily_summary(
    balance: float, equity: float, dd_pct: float,
    trades_today: int, pips_today: float,
    open_positions: int, top_lines: list[str],
):
    sign  = "+" if pips_today >= 0 else ""
    color = "✅" if pips_today >= 0 else "❌"
    perf = "\n".join(f"  {l}" for l in top_lines[:6]) if top_lines else "  Keine Daten"
    _send(
        f"📊 <b>Tages-Zusammenfassung</b>\n"
        f"Balance: <b>{balance:.2f}</b> | Equity: {equity:.2f}\n"
        f"DD: {dd_pct:.2f}%\n"
        f"{color} Heute: {trades_today} Trades | {sign}{pips_today:.1f}p\n"
        f"Offen: {open_positions}\n\n"
        f"<b>Performance:</b>\n{perf}\n"
        f"📅 {datetime.utcnow().strftime('%Y-%m-%d')} {_ts()}"
    )


def notify_meta_analysis(recommendations: str, short_summary: str):
    _send(
        f"🧠 <b>KI Meta-Analyse</b>\n\n"
        f"{html.escape(short_summary[:600])}\n\n"
        f"📋 Report: <code>meta_report.md</code>\n"
        f"⏰ {_ts()}"
    )


def notify_error(context: str, error: str):
    _send(
        f"🚨 <b>Agent Fehler</b>\n"
        f"Context: {html.escape(context)}\n"
        f"<code>{html.escape(str(error)[:200])}</code>\n"
        f"⏰ {_ts()}"
    )


def send_raw(text: str):
    _send(text)
