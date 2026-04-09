# =============================================================================
# analyzer.py — Claude als visueller Setup-Bewerter (v8)
# C:\mt5_agent\analyzer.py
#
# v8 FUNDAMENTAL-ÄNDERUNG:
#   Claude entscheidet NUR: Richtung (BUY/SELL) + Qualität (A/B/C)
#   Python berechnet: SL, TP, Lot, Management
#   Single-Agent Chain-of-Thought, keine Multi-Agent Debatte
#
# Funktionen:
#   1. analyze_charts()        — Claude bewertet: GO/NO-GO + Qualität
#   2. screen_overview()       — Claude wählt Watchlist
#   3. run_daily_reflection()  — Claude analysiert Trades
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json
import re
import time
import base64
import os

import anthropic

from config import CLAUDE_MODEL, ANTHROPIC_API_KEY, _auto_pip_value
from logger_setup import get_logger

log = get_logger("analyzer")


# =============================================================================
# API Retry für 529 Overloaded / 429 Rate Limit / 500 Server Error
# =============================================================================

def _api_call_with_retry(client, max_retries=3, base_delay=30, **kwargs):
    """Wrapper mit Retry bei API-Fehlern. Exponential Backoff: 30s → 60s → 120s."""
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.OverloadedError:
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                log.warning(f"API Overloaded (529), Retry {attempt+1}/{max_retries} in {delay}s...")
                time.sleep(delay)
            else:
                log.error(f"API Overloaded nach {max_retries} Versuchen — Skip")
                raise
        except anthropic.RateLimitError:
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                log.warning(f"API Rate Limit (429), Retry {attempt+1}/{max_retries} in {delay}s...")
                time.sleep(delay)
            else:
                raise
        except anthropic.InternalServerError:
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                log.warning(f"API Server Error (500), Retry {attempt+1}/{max_retries} in {delay}s...")
                time.sleep(delay)
            else:
                raise
        except anthropic.APIConnectionError:
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                log.warning(f"API Connection Error, Retry {attempt+1}/{max_retries} in {delay}s...")
                time.sleep(delay)
            else:
                raise


# =============================================================================
# Datenstrukturen
# =============================================================================

@dataclass
class TradeDecision:
    """Claudes Entscheidung — NUR Richtung und Qualität."""
    trade:      bool
    direction:  str      # BUY oder SELL
    quality:    str      # A, B, C
    confidence: int      # 0-100
    reasoning:  str


@dataclass
class Analysis:
    """Bridge-Objekt für agent.py Kompatibilität."""
    decision:   str      # "TRADE" oder "SKIP"
    direction:  str
    quality:    str
    confidence: int
    sl_pips:    float    # 0 — wird von Python in agent.py gefüllt
    tp_pips:    float    # 0 — wird von Python in agent.py gefüllt
    reasoning:  str


# =============================================================================
# System-Prompt: Chain-of-Thought, NUR Richtung + Qualität
# =============================================================================

TRADER_PROMPT = """Du bist ein professioneller Forex-Trader. Du bewertest Chart-Setups.

DEINE EINZIGE AUFGABE: Sag ob dieses Setup gut ist und in welche Richtung.
Du gibst KEINE Stop-Loss oder Take-Profit Levels an.

ANALYSE-PROZESS (Schritt für Schritt):

SCHRITT 1 — DAILY CHART: Was ist der dominante Trend?
- Preis über beiden EMAs = Aufwärtstrend
- Preis unter beiden EMAs = Abwärtstrend
- Zwischen den EMAs = Neutral/Unklar
- EMA-Richtung: Steigend, Fallend, Flach?

SCHRITT 2 — H4 CHART: Bestätigt H4 den D1-Trend?
- Wenn H4 gegen D1 läuft → KEIN TRADE
- Ist der Preis an einem Level (Support/Resistance/EMA)?
- Klare Struktur (Higher Highs/Lows oder Lower Highs/Lows)?

SCHRITT 3 — H1 CHART: Gibt es JETZT ein Entry-Signal?
- Kerzenformation (Engulfing, Pin Bar, Inside Bar)?
- Breakout oder Rejection?
- Ohne klares H1-Signal → KEIN TRADE

SCHRITT 4 — RISIKEN: Was könnte schief gehen?
- Nahe Resistance/Support gegen Trade-Richtung?
- Überkauft/Überverkauft nach starkem Move?
- Ein Bounce in einem Abwärtstrend ist KEIN BUY-Signal!

SCHRITT 5 — FINALE ENTSCHEIDUNG:
- NUR mit D1-Trend traden. Counter-Trend = automatisch C.
- A = Lehrbuch: Alle 3 TFs aligned, klares Signal, gutes Level
- B = Solide: Trend stimmt, Signal da, kleiner Makel
- C = Schwach: Gegen Trend, kein Signal, oder zu riskant

ANTWORT NUR als JSON:
{
    "trade": true/false,
    "direction": "BUY" oder "SELL",
    "quality": "A" oder "B" oder "C",
    "confidence": 0-100,
    "d1_trend": "UP" oder "DOWN" oder "NEUTRAL",
    "h4_confirms": true/false,
    "h1_signal": "Engulfing" oder "PinBar" oder "Breakout" oder "none",
    "main_risk": "<Größtes Risiko in einem Satz>",
    "reasoning": "<Deine Schritt-für-Schritt Analyse. 3-5 Sätze.>"
}"""


REFLECTION_PROMPT = """Du bist ein Trading-Coach der die Performance analysiert.

WICHTIG:
- Mindestens 5 Trades eines Symbols bevor du eine symbol-spezifische Regel schreibst
- Regeln müssen MESSBAR sein ("Preis unter EMA50" statt "Trend ist schwach")
- Lösche Regeln die sich als falsch erwiesen haben
- Max 30 Regeln insgesamt

ANTWORT ALS JSON: {"analysis": "...", "new_rules": ["Regel 1", "Regel 2", ...]}"""


SCREENING_PROMPT = """Du bist ein Trader der seine Watchlist erstellt.
Du siehst eine Übersicht aller D1-Charts. Jeder Chart zeigt:
- Preislinie (weiß), EMA20 (cyan), EMA50 (orange)
- Grüne Füllung = bullish, Rote Füllung = bearish

WÄHLE 3-8 Paare die JETZT am interessantesten sind:
- Klarer Trend + Preis an einem Key-Level
- Starke Momentum-Kerzen
- NICHT: Seitwärts ohne Richtung

ANTWORT NUR als JSON:
{
    "watchlist": ["EURUSD", "GBPJPY"],
    "reasoning": "EURUSD: Abwärtstrend nahe Support."
}"""


# =============================================================================
# Bild laden
# =============================================================================

def _load_image(filepath: str) -> Optional[str]:
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception as e:
        log.warning(f"Bild laden: {filepath} | {e}")
        return None


# =============================================================================
# Haupt-Analyse: Claude bewertet Setup (NUR Richtung + Qualität)
# =============================================================================

def analyze_charts(
    symbol: str,
    chart_paths: dict[str, str],
    current_price: float,
    atr_pips: float,
    memory_context: str = "",
) -> Optional[TradeDecision]:
    """
    Claude sieht Charts und entscheidet:
    - Ist das Setup gut? (trade: true/false)
    - Welche Richtung? (BUY/SELL)
    - Wie gut? (A/B/C)
    
    Claude gibt KEINE SL/TP zurück.
    """
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY nicht gesetzt!")
        return None

    images = {}
    for tf in ["D1", "H4", "H1"]:
        if tf in chart_paths:
            b64 = _load_image(chart_paths[tf])
            if b64:
                images[tf] = b64

    if not images:
        log.warning(f"{symbol}: Keine Charts — überspringe")
        return None

    # Erfahrungen laden (bleibt — das sind eigene Daten, kein externer Anker)
    experience = ""
    try:
        from trade_journal import get_experience_context
        experience = get_experience_context(symbol)
    except Exception:
        pass

    # market_intel wird NICHT an Claude gegeben (Anchoring Bias!)
    # Investing.com Signal verzerrt Claudes visuelle Analyse
    # Wird nur in Python Confluence-Score genutzt (in scanner.py)

    # Content bauen — Claude sieht NUR: Charts + Erfahrung + Memory
    content = []
    for tf in ["D1", "H4", "H1"]:
        if tf in images:
            content.append({"type": "text", "text": f"--- {tf} CHART ---"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": images[tf]}
            })

    # Textkontext: Nur neutrale Fakten, keine Richtungsaussagen
    text_parts = [f"\nSYMBOL: {symbol}", f"PREIS: {current_price}", f"ATR(14) H1: {atr_pips:.1f} Pips"]
    if experience:
        text_parts.append(f"\n{experience}")
    if memory_context:
        text_parts.append(f"\n{memory_context}")
    text_parts.append("\nAnalysiere Schritt für Schritt. JSON:")

    content.append({"type": "text", "text": "\n".join(text_parts)})

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = _api_call_with_retry(
            client,
            model=CLAUDE_MODEL,
            max_tokens=800,
            system=TRADER_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        log.debug(f"Claude ({symbol}): {raw[:300]}")
        return _parse_decision(raw, symbol)
    except Exception as e:
        log.error(f"Claude Fehler ({symbol}): {e}", exc_info=True)
        return None


def _parse_decision(raw: str, symbol: str) -> Optional[TradeDecision]:
    """Parst Claudes JSON-Antwort mit Counter-Trend Schutz."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        data = json.loads(text)

        trade = bool(data.get("trade", False))
        direction = str(data.get("direction", "")).upper()
        quality = str(data.get("quality", "C")).upper()
        confidence = int(data.get("confidence", 0))
        reasoning = str(data.get("reasoning", ""))
        d1_trend = str(data.get("d1_trend", "NEUTRAL")).upper()
        h4_confirms = bool(data.get("h4_confirms", False))
        main_risk = str(data.get("main_risk", ""))

        if direction not in ("BUY", "SELL"):
            trade = False
        if quality not in ("A", "B", "C"):
            quality = "C"

        # HARTER Counter-Trend Schutz (Python überschreibt Claude)
        if d1_trend == "DOWN" and direction == "BUY":
            log.info(f"  ⛔ Counter-Trend BLOCKED: D1 DOWN vs BUY")
            quality = "C"
            trade = False
            reasoning = f"[COUNTER-TREND] {reasoning}"
        elif d1_trend == "UP" and direction == "SELL":
            log.info(f"  ⛔ Counter-Trend BLOCKED: D1 UP vs SELL")
            quality = "C"
            trade = False
            reasoning = f"[COUNTER-TREND] {reasoning}"

        # H4 muss bestätigen bei B-Setups
        if trade and not h4_confirms and quality == "B":
            log.info(f"  ⚠️ H4 bestätigt nicht → C")
            quality = "C"
            trade = False

        decision = TradeDecision(
            trade=trade,
            direction=direction,
            quality=quality,
            confidence=max(0, min(100, confidence)),
            reasoning=f"{reasoning} Risk: {main_risk}" if main_risk else reasoning,
        )

        emoji = "✅" if trade else "❌"
        log.info(f"  {emoji} Claude: Q:{quality} {direction} D1:{d1_trend} H4:{'✓' if h4_confirms else '✗'} | {reasoning[:80]}")
        return decision

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning(f"Parse-Fehler ({symbol}): {e} | {raw[:200]}")
        return None


# =============================================================================
# Bridge für agent.py
# =============================================================================

def analyze_setup_with_charts(setup, chart_paths={}, memory_context="") -> Optional[Analysis]:
    """Bridge: Setup → Claude → Analysis (sl_pips/tp_pips = 0, Python füllt)."""
    atr_pips = getattr(setup, 'sl_pips', 20)

    decision = analyze_charts(
        symbol=setup.symbol,
        chart_paths=chart_paths,
        current_price=setup.entry_price,
        atr_pips=atr_pips,
        memory_context=memory_context,
    )

    if decision is None:
        return None

    return Analysis(
        decision="TRADE" if decision.trade and decision.quality in ("A", "B") else "SKIP",
        direction=decision.direction,
        quality=decision.quality,
        confidence=decision.confidence,
        sl_pips=0,   # Python berechnet
        tp_pips=0,   # Python berechnet
        reasoning=decision.reasoning,
    )


# =============================================================================
# Overview Screening
# =============================================================================

def screen_overview(overview_image_path: str, available_symbols: list[str]) -> list[str]:
    """Claude sieht Overview-Grid und wählt interessante Paare."""
    if not ANTHROPIC_API_KEY:
        return available_symbols[:10]

    b64 = _load_image(overview_image_path)
    if not b64:
        return available_symbols[:10]

    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": (
            f"Verfügbare Symbole: {', '.join(available_symbols)}\n"
            f"Wähle die interessantesten. JSON:"
        )},
    ]

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = _api_call_with_retry(
            client,
            model=CLAUDE_MODEL, max_tokens=500,
            system=SCREENING_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()

        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)

        watchlist = [s.upper() for s in data.get("watchlist", []) if s.upper() in available_symbols]
        reasoning = str(data.get("reasoning", ""))

        if watchlist:
            log.info(f"Claude Watchlist ({len(watchlist)}): {', '.join(watchlist)}")
            log.info(f"  Begründung: {reasoning[:120]}")
            return watchlist

    except Exception as e:
        log.warning(f"Screening Fehler: {e}")

    return available_symbols[:8]


# =============================================================================
# Reflexion (wöchentlich in v8)
# =============================================================================

def run_daily_reflection() -> Optional[str]:
    """Claude analysiert Trades/Skips und schreibt Regeln."""
    try:
        from trade_journal import get_reflection_prompt, save_reflection, get_daily_trades, get_daily_skips

        today_trades = get_daily_trades()
        today_skips = get_daily_skips()
        if not today_trades and not today_skips:
            log.info("Reflexion: Keine Trades/Skips — überspringe")
            return None

        prompt_text = get_reflection_prompt()
        if not prompt_text:
            return None

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = _api_call_with_retry(
            client,
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=REFLECTION_PROMPT,
            messages=[{"role": "user", "content": prompt_text}],
        )
        raw = response.content[0].text.strip()

        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        data = json.loads(text)
        analysis = str(data.get("analysis") or "")
        new_rules = data.get("new_rules", [])
        if isinstance(new_rules, list):
            new_rules = [str(r) for r in new_rules if r]

        save_reflection(analysis, new_rules)
        log.info(f"Reflexion: {analysis[:100]}")
        log.info(f"Neue Regeln: {len(new_rules)}")
        for r in new_rules[:5]:
            log.info(f"  • {r}")

        return analysis

    except Exception as e:
        log.error(f"Reflexion Fehler: {e}", exc_info=True)
        return None
