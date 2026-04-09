"""
fix_tp_bug.py — Patcht den TP-Bug in agent.py und executor.py
Ausführen auf dem VPS: py -3.11 fix_tp_bug.py

Bug: Wenn Claude die Richtung vom Scanner überschreibt (z.B. Scanner=SELL, Claude=BUY),
werden SL/TP nicht neu berechnet → TP landet auf der falschen Seite vom Entry.

Fix 1 (agent.py): Validierung nach SL/TP Berechnung
Fix 2 (executor.py): Sicherheits-Check vor Order-Senden
"""

import re
import shutil
from datetime import datetime

BASE = r"C:\mt5_agent"

def backup(filepath):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.backup_{ts}"
    shutil.copy2(filepath, backup_path)
    print(f"  Backup: {backup_path}")
    return backup_path

def patch_agent():
    filepath = f"{BASE}\\agent.py"
    print(f"\n=== Patching {filepath} ===")
    backup(filepath)
    
    with open(filepath, "r", encoding="utf-8") as f:
        code = f.read()
    
    # Suche die Stelle nach "analysis.tp_pips = tp_pips"
    marker = "analysis.tp_pips = tp_pips"
    
    if marker not in code:
        print("  FEHLER: Marker nicht gefunden! Datei wurde manuell geändert?")
        return False
    
    # Prüfe ob Patch schon drin ist
    if "KRITISCHE VALIDIERUNG: TP muss positiv" in code:
        print("  Patch bereits vorhanden — überspringe")
        return True
    
    patch = '''analysis.tp_pips = tp_pips

        # === KRITISCHE VALIDIERUNG: TP muss positiv und sinnvoll sein ===
        if analysis.sl_pips <= 0:
            log.warning(f"Invalid SL: {analysis.sl_pips}p — SKIP {setup.symbol}")
            return False
        if analysis.tp_pips <= 0:
            log.warning(f"Invalid TP: {analysis.tp_pips}p — korrigiere auf SL * 2.0")
            analysis.tp_pips = analysis.sl_pips * 2.0
        if analysis.tp_pips < analysis.sl_pips * MIN_RR_RATIO:
            analysis.tp_pips = analysis.sl_pips * MIN_RR_RATIO
            log.info(f"  TP korrigiert auf {analysis.tp_pips:.1f}p (Min R:R {MIN_RR_RATIO})")'''
    
    code = code.replace(marker, patch, 1)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(code)
    
    print("  agent.py gepatcht ✓")
    return True

def patch_executor():
    filepath = f"{BASE}\\executor.py"
    print(f"\n=== Patching {filepath} ===")
    backup(filepath)
    
    with open(filepath, "r", encoding="utf-8") as f:
        code = f.read()
    
    # Prüfe ob Patch schon drin ist
    if "SICHERHEITS-CHECK: TP muss auf richtiger Seite" in code:
        print("  Patch bereits vorhanden — überspringe")
        return True
    
    # Suche die Stelle vor "# --- Order senden ---"
    marker = "# --- Order senden ---"
    
    if marker not in code:
        print("  FEHLER: Marker '# --- Order senden ---' nicht gefunden!")
        return False
    
    patch = '''# === SICHERHEITS-CHECK: TP muss auf richtiger Seite sein ===
    if direction == "BUY" and tp_price <= entry_price:
        log.error(f"BLOCKED: BUY {symbol} TP {tp_price} <= Entry {entry_price} — ungültiger Trade!")
        return None
    if direction == "SELL" and tp_price >= entry_price:
        log.error(f"BLOCKED: SELL {symbol} TP {tp_price} >= Entry {entry_price} — ungültiger Trade!")
        return None
    # SL muss auch auf der richtigen Seite sein
    if direction == "BUY" and sl_price >= entry_price:
        log.error(f"BLOCKED: BUY {symbol} SL {sl_price} >= Entry {entry_price} — ungültiger Trade!")
        return None
    if direction == "SELL" and sl_price <= entry_price:
        log.error(f"BLOCKED: SELL {symbol} SL {sl_price} <= Entry {entry_price} — ungültiger Trade!")
        return None

    # --- Order senden ---'''
    
    code = code.replace(marker, patch, 1)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(code)
    
    print("  executor.py gepatcht ✓")
    return True

if __name__ == "__main__":
    print("=" * 50)
    print("TP-Bug Fix Patch")
    print("=" * 50)
    
    ok1 = patch_agent()
    ok2 = patch_executor()
    
    print("\n" + "=" * 50)
    if ok1 and ok2:
        print("FERTIG! Beide Dateien gepatcht.")
        print("\nNächste Schritte:")
        print("  1. Agent neu starten: py -3.11 agent.py")
        print("  2. Git commit:")
        print('     git add -A && git commit -m "Fix TP bug: validation + safety check" && git push')
    else:
        print("FEHLER! Prüfe die Ausgabe oben.")
        print("Backups wurden erstellt — bei Problemen wiederherstellen.")
    print("=" * 50)
