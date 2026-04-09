# =============================================================================
# memory.py — Selbstlern-Gedächtnis des Trading Agents (v4)
# C:\mt5_agent\memory.py
#
# v4 Fixes:
#   - MIN_TRADES_FOR_STATS = 20 (statt 5 — statistisch sinnvoller)
#   - Blacklist-Expiry: 14 Tage → automatisches Aufheben
#   - MEMORY_FILE aus config.py importiert
#   - Lot-Faktor Drift-Schutz (Reset Richtung 1.0 wenn neutral)
# =============================================================================

from __future__ import annotations
import json
import os
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path

from logger_setup import get_logger

log = get_logger("memory")

try:
    from config import MEMORY_FILE
except ImportError:
    MEMORY_FILE = r"C:\mt5_agent\agent_memory.json"

# Mindest-Trades bevor Blacklist/Lot-Anpassung greift
MIN_TRADES_FOR_STATS = 20

# Schwellwerte
BLACKLIST_WINRATE_THRESHOLD  = 0.30   # < 30% WR → Blacklist
LOTREDUCE_WINRATE_THRESHOLD  = 0.45   # < 45% WR → Lot -10%
LOTBOOST_WINRATE_THRESHOLD   = 0.60   # > 60% WR → Lot +5%
BLACKLIST_MIN_STREAK_LOSSES  = 5      # 5x Verlust in Folge → Warnung
BLACKLIST_LIFT_WINRATE       = 0.50   # Blacklist aufheben wenn WR erholt

# Blacklist-Expiry: nach N Tagen wird Blacklist automatisch aufgehoben
BLACKLIST_EXPIRY_DAYS = 14


# -----------------------------------------------------------------------------
# Datenstrukturen
# -----------------------------------------------------------------------------

@dataclass
class ComboStats:
    """Statistik für eine (Symbol, Pattern, Timeframe) Kombination."""
    symbol:      str
    pattern:     str
    timeframe:   str
    trades:      int   = 0
    wins:        int   = 0
    losses:      int   = 0
    total_pips:  float = 0.0
    win_pips:    float = 0.0
    loss_pips:   float = 0.0
    streak:      int   = 0      # + = Gewinnserie, - = Verlustserie
    blacklisted: bool  = False
    blacklist_reason: str = ""
    blacklisted_at: str = ""    # ISO timestamp wann Blacklist aktiviert wurde
    lot_factor:  float = 1.0
    last_update: str   = ""
    notes:       list  = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.0

    @property
    def avg_win_pips(self) -> float:
        return self.win_pips / self.wins if self.wins > 0 else 0.0

    @property
    def avg_loss_pips(self) -> float:
        return self.loss_pips / self.losses if self.losses > 0 else 0.0

    @property
    def expectancy_pips(self) -> float:
        return self.total_pips / self.trades if self.trades > 0 else 0.0

    def summary(self) -> str:
        bl = "BLACKLIST" if self.blacklisted else "aktiv"
        return (
            f"{self.symbol} {self.pattern} {self.timeframe} | "
            f"Trades:{self.trades} WR:{self.win_rate*100:.0f}% | "
            f"Avg+:{self.avg_win_pips:.1f}p Avg-:{self.avg_loss_pips:.1f}p | "
            f"Expect:{self.expectancy_pips:.1f}p | "
            f"LotFactor:{self.lot_factor:.2f} | {bl}"
        )


@dataclass
class SymbolStats:
    """Übergreifende Statistik pro Symbol."""
    symbol:       str
    trades:       int   = 0
    wins:         int   = 0
    total_pips:   float = 0.0
    lot_factor:   float = 1.0
    notes:        list  = field(default_factory=list)
    last_update:  str   = ""

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


# -----------------------------------------------------------------------------
# Memory Manager
# -----------------------------------------------------------------------------

class AgentMemory:

    def __init__(self, memory_file: str = MEMORY_FILE):
        self.memory_file = memory_file
        self.combo_stats:  dict[str, ComboStats]  = {}
        self.symbol_stats: dict[str, SymbolStats] = {}
        self.global_notes: list[str]              = []
        self._load()

    # ---- Laden / Speichern ---------------------------------------------------

    def _load(self):
        if not os.path.exists(self.memory_file):
            log.info("Keine Memory-Datei — starte mit leerem Gedächtnis")
            return
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, val in data.get("combo_stats", {}).items():
                for field_name in ("notes", ):
                    val.setdefault(field_name, [])
                for field_name in ("lot_factor",):
                    val.setdefault(field_name, 1.0)
                for field_name in ("blacklist_reason", "blacklisted_at"):
                    val.setdefault(field_name, "")
                self.combo_stats[key] = ComboStats(**val)

            for key, val in data.get("symbol_stats", {}).items():
                val.setdefault("notes", [])
                val.setdefault("lot_factor", 1.0)
                self.symbol_stats[key] = SymbolStats(**val)

            self.global_notes = data.get("global_notes", [])
            log.info(f"Memory geladen: {len(self.combo_stats)} Kombinationen")

        except Exception as e:
            log.error(f"Memory-Load Fehler: {e}")

    def save(self):
        data = {
            "combo_stats":  {k: asdict(v) for k, v in self.combo_stats.items()},
            "symbol_stats": {k: asdict(v) for k, v in self.symbol_stats.items()},
            "global_notes": self.global_notes[-50:],   # Max 50 globale Notizen
            "last_saved":   datetime.utcnow().isoformat(),
        }
        try:
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error(f"Memory-Save Fehler: {e}")

    # ---- Schlüssel -----------------------------------------------------------

    @staticmethod
    def _combo_key(symbol: str, pattern: str, timeframe: str) -> str:
        return f"{symbol}|{pattern}|{timeframe}"

    def _get_or_create_combo(self, symbol: str, pattern: str, timeframe: str) -> ComboStats:
        key = self._combo_key(symbol, pattern, timeframe)
        if key not in self.combo_stats:
            self.combo_stats[key] = ComboStats(symbol=symbol, pattern=pattern, timeframe=timeframe)
        return self.combo_stats[key]

    def _get_or_create_symbol(self, symbol: str) -> SymbolStats:
        if symbol not in self.symbol_stats:
            self.symbol_stats[symbol] = SymbolStats(symbol=symbol)
        return self.symbol_stats[symbol]

    # ---- Trade aufzeichnen ---------------------------------------------------

    def record_trade_result(
        self,
        symbol: str, pattern: str, timeframe: str,
        pips: float, ticket: int = 0,
    ):
        won = pips > 0
        ts  = datetime.utcnow().isoformat()

        combo = self._get_or_create_combo(symbol, pattern, timeframe)
        combo.trades     += 1
        combo.total_pips += pips
        combo.last_update = ts

        if won:
            combo.wins     += 1
            combo.win_pips += pips
            combo.streak    = max(0, combo.streak) + 1
        else:
            combo.losses    += 1
            combo.loss_pips += abs(pips)
            combo.streak    = min(0, combo.streak) - 1

        sym = self._get_or_create_symbol(symbol)
        sym.trades     += 1
        sym.wins       += (1 if won else 0)
        sym.total_pips += pips
        sym.last_update = ts

        self._analyze_and_adjust(combo, sym, pips, won, ticket)
        self.save()

        log.info(
            f"Memory: {combo.summary()} | "
            f"Trade: {'+'if won else ''}{pips:.1f}p (#{ticket})"
        )

    # ---- Selbst-Analyse ------------------------------------------------------

    def _analyze_and_adjust(self, combo: ComboStats, sym: SymbolStats,
                            pips: float, won: bool, ticket: int):
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

        if combo.trades < MIN_TRADES_FOR_STATS:
            return

        wr     = combo.win_rate
        exp    = combo.expectancy_pips
        streak = combo.streak

        # 1. BLACKLIST
        if not combo.blacklisted and wr < BLACKLIST_WINRATE_THRESHOLD:
            reason = (
                f"WR {wr*100:.0f}% < {BLACKLIST_WINRATE_THRESHOLD*100:.0f}% "
                f"nach {combo.trades} Trades | Expect: {exp:.1f}p"
            )
            combo.blacklisted      = True
            combo.blacklist_reason  = reason
            combo.blacklisted_at   = datetime.utcnow().isoformat()
            note = f"[{ts}] BLACKLIST: {reason}"
            combo.notes.append(note)
            self.global_notes.append(f"{combo.symbol} {combo.pattern} {combo.timeframe} — {note}")
            log.warning(f"Combo blacklisted: {combo.symbol} {combo.pattern} {combo.timeframe}")

        # Blacklist aufheben: WR erholt ODER Expiry abgelaufen
        elif combo.blacklisted:
            lift = False
            lift_reason = ""

            # WR erholt
            if wr >= BLACKLIST_LIFT_WINRATE and combo.trades >= MIN_TRADES_FOR_STATS + 5:
                lift = True
                lift_reason = f"WR erholt auf {wr*100:.0f}%"

            # Expiry (14 Tage)
            if combo.blacklisted_at:
                try:
                    bl_date = datetime.fromisoformat(combo.blacklisted_at)
                    if datetime.utcnow() - bl_date > timedelta(days=BLACKLIST_EXPIRY_DAYS):
                        lift = True
                        lift_reason = f"Expiry nach {BLACKLIST_EXPIRY_DAYS} Tagen"
                except:
                    pass

            if lift:
                combo.blacklisted = False
                combo.blacklist_reason = ""
                combo.blacklisted_at = ""
                note = f"[{ts}] Blacklist aufgehoben: {lift_reason}"
                combo.notes.append(note)
                log.info(f"Blacklist aufgehoben: {combo.symbol} {combo.pattern} {combo.timeframe} ({lift_reason})")

        # 2. LOT-FAKTOR (Combo)
        old_factor = combo.lot_factor

        if wr < LOTREDUCE_WINRATE_THRESHOLD:
            combo.lot_factor = max(0.50, combo.lot_factor - 0.10)
        elif wr > LOTBOOST_WINRATE_THRESHOLD and exp > 5:
            combo.lot_factor = min(1.50, combo.lot_factor + 0.05)
        else:
            # Neutral-Zone: langsam Richtung 1.0 driften
            if combo.lot_factor < 0.95:
                combo.lot_factor = min(1.0, combo.lot_factor + 0.02)
            elif combo.lot_factor > 1.05:
                combo.lot_factor = max(1.0, combo.lot_factor - 0.02)

        if abs(combo.lot_factor - old_factor) > 0.01:
            arrow = "↓" if combo.lot_factor < old_factor else "↑"
            note = f"[{ts}] Lot {arrow}: {old_factor:.2f} → {combo.lot_factor:.2f} (WR {wr*100:.0f}%)"
            combo.notes.append(note)

        # 3. VERLUSTSERIE
        if streak <= -BLACKLIST_MIN_STREAK_LOSSES:
            note = f"[{ts}] Verlustserie: {abs(streak)}x (#{ticket})"
            combo.notes.append(note)
            log.warning(f"Verlustserie {abs(streak)}x: {combo.symbol} {combo.pattern}")

        # 4. Symbol-Lot
        if sym.trades >= MIN_TRADES_FOR_STATS:
            sym_wr  = sym.win_rate
            old_sym = sym.lot_factor
            if sym_wr < 0.40:
                sym.lot_factor = max(0.60, sym.lot_factor - 0.10)
            elif sym_wr > 0.65 and sym.total_pips > 20:
                sym.lot_factor = min(1.30, sym.lot_factor + 0.05)

        # Notes trimmen (max 20 pro Combo)
        if len(combo.notes) > 20:
            combo.notes = combo.notes[-20:]
        if len(sym.notes) > 10:
            sym.notes = sym.notes[-10:]

    # ---- Abfragen ------------------------------------------------------------

    def is_blacklisted(self, symbol: str, pattern: str, timeframe: str) -> tuple[bool, str]:
        key = self._combo_key(symbol, pattern, timeframe)
        if key in self.combo_stats:
            c = self.combo_stats[key]
            if c.blacklisted:
                return True, c.blacklist_reason
        return False, ""

    def get_lot_factor(self, symbol: str, pattern: str, timeframe: str) -> float:
        combo_factor = 1.0
        key = self._combo_key(symbol, pattern, timeframe)
        if key in self.combo_stats:
            combo_factor = self.combo_stats[key].lot_factor

        sym_factor = 1.0
        if symbol in self.symbol_stats:
            sym_factor = self.symbol_stats[symbol].lot_factor

        combined = combo_factor * sym_factor
        return max(0.40, min(1.50, round(combined, 2)))

    def get_context_for_claude(self, symbol: str, pattern: str, timeframe: str) -> str:
        lines = []

        key = self._combo_key(symbol, pattern, timeframe)
        if key in self.combo_stats:
            c = self.combo_stats[key]
            if c.trades >= 3:
                lines.append(
                    f"PERFORMANCE HISTORY ({c.symbol} {c.pattern} {c.timeframe}): "
                    f"{c.trades} Trades | WR: {c.win_rate*100:.0f}% | "
                    f"Avg+: {c.avg_win_pips:.1f}p | Avg-: {c.avg_loss_pips:.1f}p | "
                    f"Expect: {c.expectancy_pips:.1f}p/Trade"
                )
                if c.streak <= -3:
                    lines.append(f"WARNUNG: Verlustserie {abs(c.streak)}x!")
                if c.notes:
                    lines.append(f"Notiz: {c.notes[-1]}")

        if symbol in self.symbol_stats:
            s = self.symbol_stats[symbol]
            if s.trades >= 3:
                lines.append(
                    f"SYMBOL GESAMT ({symbol}): "
                    f"{s.trades} Trades | WR: {s.win_rate*100:.0f}% | "
                    f"Total: {s.total_pips:.0f}p"
                )

        if not lines:
            lines.append("PERFORMANCE HISTORY: Noch keine Daten (< 3 Trades)")

        return "\n".join(lines)

    def print_full_report(self):
        log.info("=" * 70)
        log.info("AGENT MEMORY — PERFORMANCE REPORT")
        log.info("=" * 70)

        if not self.combo_stats:
            log.info("  Noch keine Trades aufgezeichnet")
            return

        sorted_combos = sorted(
            self.combo_stats.values(),
            key=lambda c: c.expectancy_pips,
            reverse=True,
        )

        for c in sorted_combos:
            if c.trades > 0:
                log.info(f"  {c.summary()}")
                for note in c.notes[-3:]:
                    log.info(f"    {note}")

        log.info("Global-Notizen (letzte 5):")
        for note in self.global_notes[-5:]:
            log.info(f"  {note}")
        log.info("=" * 70)


# Singleton
_memory_instance: Optional[AgentMemory] = None


def get_memory() -> AgentMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = AgentMemory()
    return _memory_instance
