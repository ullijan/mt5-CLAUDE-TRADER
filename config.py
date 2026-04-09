# =============================================================================
# config.py — AI Trading Agent Konfiguration (v4)
# C:\mt5_agent\config.py
#
# WICHTIG: Vor erstem Start prüfen:
#   1. MT5_TERMINAL_PATH → Pfad zum dedizierten MT5 für den Agent
#   2. SYMBOLS → Im MT5 Market Watch prüfen (Broker-Suffixe!)
#   3. ANTHROPIC_API_KEY → als Umgebungsvariable oder direkt eintragen
#   4. TELEGRAM_TOKEN/CHAT_ID → optional, für Benachrichtigungen
# =============================================================================

import os

# -----------------------------------------------------------------------------
# Account-Sicherheit
# -----------------------------------------------------------------------------
# Agent startet NUR wenn dieser Account eingeloggt ist.
# Verhindert versehentliches Traden auf dem falschen Account.
EXPECTED_ACCOUNT = 52826257   # ICMarkets Demo

# Demo-Modus: Logging ist ausführlicher, keine Prod-Warnungen
DEMO_MODE = True

# -----------------------------------------------------------------------------
# MT5 Terminal Verbindung
# -----------------------------------------------------------------------------
# Pfad zum MT5 Terminal exe (für den Trading-Account!)
# MUSS gesetzt sein wenn mehrere MT5-Instanzen laufen.
MT5_TERMINAL_PATH = r"C:\Program Files\MetaTrader 5 IC Markets EU_CLAUDE TRADER\terminal64.exe"

MT5_ACCOUNT  = 52826257
MT5_PASSWORD = "$4h0SnXXW3UC0a"
MT5_SERVER   = "ICMarketsEU-Demo"

# -----------------------------------------------------------------------------
# Symbole & Timeframes
# -----------------------------------------------------------------------------
# ICMarkets EU Demo — spread-günstige Paare
# Alle Symbole müssen im MT5 Market Watch sichtbar sein!
# Agent prüft beim Start ob Symbol verfügbar ist → fehlende werden übersprungen
SYMBOLS = {
    # === MAJORS (engste Spreads, ~0.1-0.5 Pips) ===
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    "USDCAD": "USDCAD",

    # === CROSSES (Spreads ~0.5-2.0 Pips) ===
    "EURJPY": "EURJPY",
    "EURGBP": "EURGBP",
    "EURAUD": "EURAUD",
    "EURCHF": "EURCHF",
    "EURCAD": "EURCAD",
    "EURNZD": "EURNZD",
    "GBPJPY": "GBPJPY",
    "GBPAUD": "GBPAUD",
    "GBPCAD": "GBPCAD",
    "GBPCHF": "GBPCHF",
    "GBPNZD": "GBPNZD",
    "AUDJPY": "AUDJPY",
    "AUDNZD": "AUDNZD",
    "AUDCAD": "AUDCAD",
    "AUDCHF": "AUDCHF",
    "NZDJPY": "NZDJPY",
    "NZDCAD": "NZDCAD",
    "CADJPY": "CADJPY",
    "CADCHF": "CADCHF",
    "CHFJPY": "CHFJPY",

    # === METALLE ===
    "XAUUSD": "XAUUSD",     # Gold
    "XAGUSD": "XAGUSD",     # Silber

    # === ROHSTOFFE ===
    "XTIUSD": "XTIUSD",     # WTI Öl

    # === INDIZES (optional — auskommentieren wenn nicht gewünscht) ===
    # "US30":   "US30",       # Dow Jones
    # "US500":  "US500",      # S&P 500
    # "AUS200": "AUS200",     # ASX 200
}

TIMEFRAMES_SCAN = ["M15", "H1", "H4"]
BARS_TO_FETCH   = 100   # OHLC-Bars pro TF für Pattern-Erkennung

# -----------------------------------------------------------------------------
# Pip-Definitionen pro Symbol
# -----------------------------------------------------------------------------
# Pip = kleinste sinnvolle Preiseinheit für SL/TP Berechnung
# Forex (4/5-stellig): 1 Pip = 0.0001 | JPY-Paare: 1 Pip = 0.01
# Gold: 0.10 | Silber: 0.01 | Indizes: 1.0
#
# WICHTIG: Für Symbole die hier NICHT stehen, wird automatisch erkannt:
#   - Symbol enthält "JPY" → 0.01
#   - Symbol enthält "XAU" → 0.10
#   - Symbol enthält "XAG" → 0.01
#   - Sonst → 0.0001
PIP_DEFINITIONS = {
    "XAUUSD": 0.10,
    "XAGUSD": 0.01,
    "XTIUSD": 0.01,    # Öl: 1 Pip = 1 Cent ($0.01)
}

def _auto_pip_value(symbol: str) -> float:
    """Auto-Detect Pip-Wert basierend auf Symbol-Name."""
    s = symbol.upper()
    if s in PIP_DEFINITIONS:
        return PIP_DEFINITIONS[s]
    if "JPY" in s:
        return 0.01
    if "XAU" in s:
        return 0.10
    if "XAG" in s:
        return 0.01
    if "XTI" in s or "OIL" in s or "WTI" in s:
        return 0.01
    if any(idx in s for idx in ("US30", "US500", "AUS200", "STOXX", "UK100", "JP225", "F40")):
        return 1.0
    return 0.0001  # Standard Forex

# -----------------------------------------------------------------------------
# Zeitsteuerung
# -----------------------------------------------------------------------------
SCAN_INTERVAL_SEC   = 30 * 60   # Overview + Deep Analysis alle 30 Min
MANAGE_INTERVAL_SEC = 30 * 60   # Position-Management alle 30 Min

# -----------------------------------------------------------------------------
# Risk-Regeln (HART — niemals überschreiben!)
# -----------------------------------------------------------------------------
MAX_OPEN_TRADES    = 10      # Technisches Maximum (MM entscheidet wirklich)
DAILY_DD_LIMIT_PCT = 4.0     # 4% des Accounts → Stopp aller neuen Orders
MAX_RISK_PER_TRADE_PCT = 1.0 # 1% pro Trade
MAX_TOTAL_EXPOSURE_PCT = 5.0 # Max 5% Gesamt-Exposure über alle offenen Trades

# Min. Confluence-Score für Claude-Analyse (1-5)
# Unter diesem Wert wird das Setup gar nicht erst an Claude geschickt
MIN_CONFLUENCE = 3

# Max 1 Trade pro Symbol gleichzeitig
MAX_TRADES_PER_SYMBOL = 1

# Cooldown nach Close: Minuten warten bevor gleiche Combo wieder getradet wird
COOLDOWN_MINUTES = 60

# -----------------------------------------------------------------------------
# Trade-Management
# -----------------------------------------------------------------------------
BE_TRIGGER_PCT = 0.50    # SL auf BE schieben wenn 50% des TP erreicht
BE_OFFSET_PIPS = 2.0     # BE + 2 Pips Puffer (Spread-Schutz)

# Frühzeitiger Exit bei H4-Struktur-Bruch
EARLY_EXIT_ON_STRUCTURE_BREAK = True

# -----------------------------------------------------------------------------
# Lot-Größen pro Symbol (Basis-Lot, wird durch ATR + Memory + Dollar-Cap angepasst)
# Für Symbole die nicht gelistet sind → DEFAULT_LOT
DEFAULT_LOT = 0.01
LOT_SIZES = {
    "XAUUSD": 0.01,    # Gold: kleiner wegen höherer Pip-Value
    "XAGUSD": 0.03,    # Silber
    "XTIUSD": 0.05,    # Öl: $0.01 pro Pip pro 0.01 Lot → 0.05 Lot = moderate Pos
    # Alle Forex-Paare bekommen DEFAULT_LOT (0.01)
}

# -----------------------------------------------------------------------------
# Magic Numbers — eine pro Setup-Typ
# -----------------------------------------------------------------------------
MAGIC_NUMBERS = {
    "InsideBar":  21001,
    "Breakout":   21002,
    "Engulfing":  21003,
    "SR_Bounce":  21004,
    "Trend":      21005,
}

# -----------------------------------------------------------------------------
# SL-Grenzen pro Symbol-Typ (in Pips)
# Für Symbole die nicht gelistet sind → Defaults basierend auf Typ
# JPY-Paare haben gleiche Pip-Grenzen wie Standard-Forex (in Pips gemessen)
DEFAULT_MIN_SL = 10   # Pips
DEFAULT_MAX_SL = 80   # Pips

MIN_SL_PIPS = {
    "XAUUSD": 80,     # Gold: höhere Volatilität
    "XAGUSD": 30,     # Silber
    "XTIUSD": 30,     # Öl: 30 Cents minimum SL
}
MAX_SL_PIPS = {
    "XAUUSD": 350,
    "XAGUSD": 150,
    "XTIUSD": 200,    # Öl: max $2.00 SL
}

# Mindest-R:R Verhältnis
MIN_RR_RATIO = 1.5

# -----------------------------------------------------------------------------
# ATR-Volatilitäts-Schwellwerte
# Für Symbole nicht gelistet → ATR_VOL_DEFAULT
ATR_VOL_THRESHOLDS = {
    "XAUUSD": {"high": 0.015, "low": 0.005},   # Gold: volatiler
    "XAGUSD": {"high": 0.020, "low": 0.006},   # Silber: noch volatiler
    "XTIUSD": {"high": 0.025, "low": 0.008},   # Öl: sehr volatil (Geopolitik, OPEC)
}

# Fallback für unbekannte Symbole
ATR_VOL_DEFAULT = {"high": 0.008, "low": 0.002}

# -----------------------------------------------------------------------------
# Mindest-Qualität für Ausführung
# A = sehr sauber, B = gut, C = schwach → C wird übersprungen
# -----------------------------------------------------------------------------
MIN_QUALITY = {"A", "B"}

# -----------------------------------------------------------------------------
# Claude API
# -----------------------------------------------------------------------------
# Sonnet für Trade-Analyse (schnell + günstig, reicht für Pattern-Bewertung)
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Opus nur für Meta-Analyse (tiefere strategische Bewertung, 1x täglich)
CLAUDE_META_MODEL = "claude-sonnet-4-20250514"   # Erstmal auch Sonnet, Opus optional

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CLAUDE_MAX_TOKENS = 800       # Trade-Analyse
CLAUDE_META_MAX_TOKENS = 3000 # Meta-Analyse

# -----------------------------------------------------------------------------
# Dateipfade
# -----------------------------------------------------------------------------
BASE_DIR    = r"C:\mt5_agent"
LOG_FILE    = rf"{BASE_DIR}\agent.log"
STATE_FILE  = rf"{BASE_DIR}\agent_state.json"
TRADE_LOG   = rf"{BASE_DIR}\trades.csv"
MEMORY_FILE = rf"{BASE_DIR}\agent_memory.json"

# -----------------------------------------------------------------------------
# Telegram Benachrichtigungen
# -----------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

NOTIFY_TRADE_OPEN    = True
NOTIFY_TRADE_CLOSE   = True
NOTIFY_BREAKEVEN     = True
NOTIFY_BLACKLIST     = True
NOTIFY_DD_WARNING    = True
NOTIFY_DAILY_SUMMARY = True
NOTIFY_META_ANALYSIS = True

# -----------------------------------------------------------------------------
# Meta-Analyse Schedule
# -----------------------------------------------------------------------------
META_ANALYSIS_HOUR_UTC = 22   # Täglich um 22:00 UTC

# -----------------------------------------------------------------------------
# Markt-Öffnungszeiten (Forex/Gold)
# -----------------------------------------------------------------------------
# Forex/Gold: Sonntag 22:00 UTC → Freitag 22:00 UTC
# Agent startet Scan 1 Stunde vorher (Sonntag 21:00 UTC)
MARKET_OPEN_DAY   = 6    # Sonntag (0=Montag, 6=Sonntag)
MARKET_OPEN_HOUR  = 21   # 21:00 UTC (1h vor Marktöffnung 22:00)
MARKET_CLOSE_DAY  = 4    # Freitag
MARKET_CLOSE_HOUR = 22   # 22:00 UTC (NY Close)

