# =============================================================================
# risk_manager.py — Risiko-Management und Trade-Freigabe
# C:\mt5_agent\risk_manager.py
#
# Prüft vor jedem Trade:
#   1. Daily Drawdown Limit nicht überschritten
#   2. Max gleichzeitige Trades nicht erreicht
#   3. Max 1 Trade pro Symbol
#   4. Cooldown nach letztem Close
#   5. Dollar-Risiko pro Trade berechnen
#
# Speichert Start-Balance für tägliches DD-Tracking.
# =============================================================================

from __future__ import annotations
import json
import os
from datetime import datetime, date, timedelta
from typing import Optional

import MetaTrader5 as mt5

from config import (
    MAX_OPEN_TRADES, DAILY_DD_LIMIT_PCT, MAX_RISK_PER_TRADE_PCT,
    MAX_TRADES_PER_SYMBOL, COOLDOWN_MINUTES, MAX_TOTAL_EXPOSURE_PCT,
    MAGIC_NUMBERS, _auto_pip_value, BASE_DIR, EXPECTED_ACCOUNT,
)
from logger_setup import get_logger

log = get_logger("risk")

_DD_STATE_FILE = os.path.join(BASE_DIR, "dd_state.json")


class RiskManager:
    """
    Zentraler Risk-Manager.
    Wird einmal beim Agent-Start instanziiert.
    """

    def __init__(self):
        self._agent_magics = set(MAGIC_NUMBERS.values())
        self._start_balance = 0.0
        self._current_date = ""
        self._last_close_times: dict[str, datetime] = {}   # symbol → letzte Close-Zeit
        self._load_dd_state()
        self._refresh_start_balance()

    # -------------------------------------------------------------------------
    # DD-State laden/speichern (überlebt Restart innerhalb eines Tages)
    # -------------------------------------------------------------------------

    def _load_dd_state(self):
        if not os.path.exists(_DD_STATE_FILE):
            return
        try:
            with open(_DD_STATE_FILE, "r") as f:
                data = json.load(f)
            saved_date = data.get("date", "")
            if saved_date == date.today().isoformat():
                self._start_balance = data.get("start_balance", 0.0)
                self._current_date  = saved_date
                log.info(f"DD-State geladen: Start-Balance {self._start_balance:.2f} für {saved_date}")
        except Exception as e:
            log.warning(f"DD-State laden: {e}")

    def _save_dd_state(self):
        try:
            with open(_DD_STATE_FILE, "w") as f:
                json.dump({
                    "date":          self._current_date,
                    "start_balance": self._start_balance,
                    "saved_at":      datetime.utcnow().isoformat(),
                }, f, indent=2)
        except Exception as e:
            log.warning(f"DD-State speichern: {e}")

    def _refresh_start_balance(self):
        """Setzt Start-Balance am Tagesbeginn (oder beim ersten Aufruf)."""
        today = date.today().isoformat()
        if self._current_date == today and self._start_balance > 0:
            return   # Schon gesetzt für heute

        acc = mt5.account_info()
        if acc:
            self._start_balance = acc.balance
            self._current_date  = today
            self._save_dd_state()
            log.info(f"Start-Balance für {today}: {self._start_balance:.2f} {acc.currency}")

    # -------------------------------------------------------------------------
    # Agent-Positionen
    # -------------------------------------------------------------------------

    def _get_agent_positions(self) -> list:
        """Alle offenen Agent-Positionen."""
        positions = mt5.positions_get() or []
        return [p for p in positions if p.magic in self._agent_magics]

    def _count_agent_trades(self) -> int:
        return len(self._get_agent_positions())

    def _count_symbol_trades(self, symbol: str) -> int:
        return sum(1 for p in self._get_agent_positions() if p.symbol == symbol)

    # -------------------------------------------------------------------------
    # Daily Drawdown
    # -------------------------------------------------------------------------

    def daily_dd_pct(self) -> float:
        """Aktueller Daily Drawdown in Prozent."""
        self._refresh_start_balance()
        if self._start_balance <= 0:
            return 0.0

        acc = mt5.account_info()
        if not acc:
            return 0.0

        # DD = (Start-Balance - aktuelle Equity) / Start-Balance × 100
        dd = (self._start_balance - acc.equity) / self._start_balance * 100
        return max(0.0, dd)

    def is_daily_dd_exceeded(self) -> bool:
        """True wenn Daily DD Limit erreicht ist."""
        dd = self.daily_dd_pct()
        return dd >= DAILY_DD_LIMIT_PCT

    # -------------------------------------------------------------------------
    # Dollar-Risiko pro Trade
    # -------------------------------------------------------------------------

    def max_risk_dollars(self) -> float:
        """Max erlaubtes Risiko in Dollar für einen einzelnen Trade."""
        acc = mt5.account_info()
        if not acc:
            return 0.0
        return acc.balance * MAX_RISK_PER_TRADE_PCT / 100.0

    def calculate_risk_dollars(self, symbol: str, lot: float, sl_pips: float) -> float:
        """
        Berechnet das Dollar-Risiko eines Trades.
        Berücksichtigt Profit-Währung (JPY-Paare, etc.)
        """
        pip = _auto_pip_value(symbol)
        if pip == 0:
            return 0.0

        info = mt5.symbol_info(symbol)
        if info and info.trade_contract_size > 0:
            # Pip-Value in Profit-Währung
            pip_value_in_profit_ccy = pip * info.trade_contract_size

            # Konvertierung in USD wenn nötig
            profit_ccy = info.currency_profit if info else "USD"
            if profit_ccy == "USD":
                pip_value_usd = pip_value_in_profit_ccy
            elif profit_ccy == "JPY":
                # JPY → USD: teile durch USDJPY Kurs
                usdjpy = mt5.symbol_info_tick("USDJPY")
                rate = usdjpy.bid if usdjpy else 150.0   # Fallback
                pip_value_usd = pip_value_in_profit_ccy / rate
            elif profit_ccy == "GBP":
                gbpusd = mt5.symbol_info_tick("GBPUSD")
                rate = gbpusd.bid if gbpusd else 1.30
                pip_value_usd = pip_value_in_profit_ccy * rate
            elif profit_ccy == "EUR":
                eurusd = mt5.symbol_info_tick("EURUSD")
                rate = eurusd.bid if eurusd else 1.10
                pip_value_usd = pip_value_in_profit_ccy * rate
            elif profit_ccy == "AUD":
                audusd = mt5.symbol_info_tick("AUDUSD")
                rate = audusd.bid if audusd else 0.65
                pip_value_usd = pip_value_in_profit_ccy * rate
            elif profit_ccy == "NZD":
                nzdusd = mt5.symbol_info_tick("NZDUSD")
                rate = nzdusd.bid if nzdusd else 0.58
                pip_value_usd = pip_value_in_profit_ccy * rate
            elif profit_ccy == "CAD":
                usdcad = mt5.symbol_info_tick("USDCAD")
                rate = usdcad.bid if usdcad else 1.38
                pip_value_usd = pip_value_in_profit_ccy / rate
            elif profit_ccy == "CHF":
                usdchf = mt5.symbol_info_tick("USDCHF")
                rate = usdchf.bid if usdchf else 0.88
                pip_value_usd = pip_value_in_profit_ccy / rate
            else:
                pip_value_usd = pip_value_in_profit_ccy   # Annäherung
        else:
            pip_value_usd = pip * 100000   # Fallback Standard Forex

        risk = sl_pips * pip_value_usd * lot
        return round(risk, 2)

    # -------------------------------------------------------------------------
    # Cooldown
    # -------------------------------------------------------------------------

    def record_close(self, symbol: str):
        """Notiert Close-Zeitpunkt für Cooldown."""
        self._last_close_times[symbol] = datetime.utcnow()

    def _is_in_cooldown(self, symbol: str) -> bool:
        """True wenn Symbol noch im Cooldown ist."""
        last = self._last_close_times.get(symbol)
        if not last:
            return False
        elapsed = (datetime.utcnow() - last).total_seconds() / 60.0
        return elapsed < COOLDOWN_MINUTES

    # -------------------------------------------------------------------------
    # Gesamt-Exposure berechnen
    # -------------------------------------------------------------------------

    def _total_exposure_pct(self) -> float:
        """Gesamt-Exposure aller offenen Agent-Trades in % der Balance."""
        acc = mt5.account_info()
        if not acc or acc.balance <= 0:
            return 0.0

        positions = self._get_agent_positions()
        if not positions:
            return 0.0

        total_risk = 0.0
        for pos in positions:
            pip = _auto_pip_value(pos.symbol)
            if pip > 0 and pos.sl != 0:
                sl_pips = abs(pos.price_open - pos.sl) / pip
                risk = self.calculate_risk_dollars(pos.symbol, pos.volume, sl_pips)
                total_risk += risk
            else:
                total_risk += acc.balance * 0.01   # 1% Schätzung wenn kein SL

        return round((total_risk / acc.balance) * 100, 2)

    # -------------------------------------------------------------------------
    # Haupt-Check: Darf ein Trade geöffnet werden?
    # -------------------------------------------------------------------------

    def can_open_trade(self, symbol: str) -> tuple[bool, str]:
        """
        Prüft ob ein neuer Trade erlaubt ist.
        MM-Regeln (in Priorität):
          1. Account-Check (richtiger Account?)
          2. Daily DD Limit (4%)
          3. Gesamt-Exposure Limit (5%)
          4. Max 1 Trade pro Symbol
          5. Cooldown nach Close
          6. DD-Budget für nächsten Trade
          7. Technisches Max-Limit (Sicherheitsnetz)
        """
        # 1. Account-Check
        acc = mt5.account_info()
        if not acc:
            return False, "Kein Account-Info"

        if EXPECTED_ACCOUNT > 0 and acc.login != EXPECTED_ACCOUNT:
            return False, f"FALSCHER ACCOUNT! #{acc.login} statt #{EXPECTED_ACCOUNT}"

        # 2. Daily DD
        dd = self.daily_dd_pct()
        if dd >= DAILY_DD_LIMIT_PCT:
            return False, f"Daily DD {dd:.2f}% >= Limit {DAILY_DD_LIMIT_PCT}%"

        # 3. Gesamt-Exposure Check (WICHTIGSTER MM-CHECK)
        exposure = self._total_exposure_pct()
        if exposure >= MAX_TOTAL_EXPOSURE_PCT:
            return False, f"Gesamt-Exposure {exposure:.1f}% >= Limit {MAX_TOTAL_EXPOSURE_PCT}%"

        # 4. Max 1 Trade pro Symbol
        sym_count = self._count_symbol_trades(symbol)
        if sym_count >= MAX_TRADES_PER_SYMBOL:
            return False, f"Max Trades für {symbol}: {sym_count}/{MAX_TRADES_PER_SYMBOL}"

        # 5. Cooldown
        if self._is_in_cooldown(symbol):
            return False, f"{symbol} im Cooldown ({COOLDOWN_MINUTES}min)"

        # 6. DD-Budget für nächsten Trade
        remaining_dd = DAILY_DD_LIMIT_PCT - dd
        if remaining_dd < MAX_RISK_PER_TRADE_PCT:
            return False, f"Zu wenig DD-Budget: {remaining_dd:.2f}% frei, brauche {MAX_RISK_PER_TRADE_PCT}%"

        # 7. Exposure-Budget für nächsten Trade
        remaining_exposure = MAX_TOTAL_EXPOSURE_PCT - exposure
        if remaining_exposure < MAX_RISK_PER_TRADE_PCT:
            return False, f"Zu wenig Exposure-Budget: {remaining_exposure:.1f}% frei"

        # 8. Technisches Maximum (Sicherheitsnetz)
        open_count = self._count_agent_trades()
        if open_count >= MAX_OPEN_TRADES:
            return False, f"Max Trades erreicht: {open_count}/{MAX_OPEN_TRADES}"

        return True, ""

    # -------------------------------------------------------------------------
    # Risiko-Validierung für konkreten Trade
    # -------------------------------------------------------------------------

    def validate_trade_risk(self, symbol: str, lot: float, sl_pips: float) -> tuple[bool, str]:
        """
        Prüft ob das Dollar-Risiko eines konkreten Trades akzeptabel ist.

        Returns:
            (True, "") wenn OK
            (False, Grund) wenn zu riskant
        """
        risk_dollars = self.calculate_risk_dollars(symbol, lot, sl_pips)
        max_risk     = self.max_risk_dollars()

        if max_risk <= 0:
            return False, "Kann max_risk nicht berechnen"

        if risk_dollars > max_risk:
            return False, (
                f"Risiko ${risk_dollars:.2f} > Max ${max_risk:.2f} "
                f"({MAX_RISK_PER_TRADE_PCT}% von Balance)"
            )

        log.debug(f"Risk-Check OK: ${risk_dollars:.2f} / ${max_risk:.2f} max")
        return True, ""

    # -------------------------------------------------------------------------
    # Status-Logging
    # -------------------------------------------------------------------------

    def log_status(self):
        """Loggt aktuellen Risiko-Status."""
        acc = mt5.account_info()
        if not acc:
            return

        dd         = self.daily_dd_pct()
        exposure   = self._total_exposure_pct()
        open_count = self._count_agent_trades()
        max_risk   = self.max_risk_dollars()

        log.info(
            f"Risk: Bal {acc.balance:.2f} | Eq {acc.equity:.2f} | "
            f"DD {dd:.2f}%/{DAILY_DD_LIMIT_PCT}% | "
            f"Exposure {exposure:.1f}%/{MAX_TOTAL_EXPOSURE_PCT}% | "
            f"Trades {open_count} | MaxRisk ${max_risk:.2f}"
        )
