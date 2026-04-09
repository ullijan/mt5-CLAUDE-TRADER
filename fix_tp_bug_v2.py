"""
fix_tp_bug_v2.py - Patcht TP-Bug mit korrekter Einrückung
"""
import shutil
from datetime import datetime

BASE = r"C:\mt5_agent"

def patch_agent():
    filepath = f"{BASE}\\agent.py"
    print(f"Patching {filepath}...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # Finde die Zeile "analysis.tp_pips = tp_pips"
    insert_after = None
    indent = ""
    for i, line in enumerate(lines):
        if "analysis.tp_pips = tp_pips" in line and "KRITISCHE" not in lines[i+1] if i+1 < len(lines) else True:
            insert_after = i
            # Einrückung von dieser Zeile übernehmen
            indent = line[:len(line) - len(line.lstrip())]
            break
    
    if insert_after is None:
        print("  FEHLER: 'analysis.tp_pips = tp_pips' nicht gefunden!")
        return False
    
    if any("KRITISCHE VALIDIERUNG" in l for l in lines):
        print("  Patch bereits vorhanden")
        return True
    
    # Patch-Zeilen mit korrekter Einrückung
    patch_lines = [
        f"\n",
        f"{indent}# === KRITISCHE VALIDIERUNG: TP muss positiv und sinnvoll sein ===\n",
        f"{indent}if analysis.sl_pips <= 0:\n",
        f"{indent}    log.warning(f\"Invalid SL: {{analysis.sl_pips}}p — SKIP {{setup.symbol}}\")\n",
        f"{indent}    return False\n",
        f"{indent}if analysis.tp_pips <= 0:\n",
        f"{indent}    log.warning(f\"Invalid TP: {{analysis.tp_pips}}p — korrigiere auf SL * 2.0\")\n",
        f"{indent}    analysis.tp_pips = analysis.sl_pips * 2.0\n",
        f"{indent}if analysis.tp_pips < analysis.sl_pips * MIN_RR_RATIO:\n",
        f"{indent}    analysis.tp_pips = analysis.sl_pips * MIN_RR_RATIO\n",
        f"{indent}    log.info(f\"  TP korrigiert auf {{analysis.tp_pips:.1f}}p (Min R:R {{MIN_RR_RATIO}})\")\n",
    ]
    
    # Einfügen nach der gefundenen Zeile
    for j, pl in enumerate(patch_lines):
        lines.insert(insert_after + 1 + j, pl)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    print("  agent.py gepatcht ✓")
    return True

def patch_executor():
    filepath = f"{BASE}\\executor.py"
    print(f"Patching {filepath}...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    if any("SICHERHEITS-CHECK: TP muss auf richtiger Seite" in l for l in lines):
        print("  Patch bereits vorhanden")
        return True
    
    # Finde "# --- Order senden ---"
    insert_before = None
    indent = ""
    for i, line in enumerate(lines):
        if "# --- Order senden ---" in line:
            insert_before = i
            indent = line[:len(line) - len(line.lstrip())]
            break
    
    if insert_before is None:
        print("  FEHLER: '# --- Order senden ---' nicht gefunden!")
        return False
    
    patch_lines = [
        f"{indent}# === SICHERHEITS-CHECK: TP muss auf richtiger Seite sein ===\n",
        f"{indent}if direction == \"BUY\" and tp_price <= entry_price:\n",
        f"{indent}    log.error(f\"BLOCKED: BUY {{symbol}} TP {{tp_price}} <= Entry {{entry_price}}\")\n",
        f"{indent}    return None\n",
        f"{indent}if direction == \"SELL\" and tp_price >= entry_price:\n",
        f"{indent}    log.error(f\"BLOCKED: SELL {{symbol}} TP {{tp_price}} >= Entry {{entry_price}}\")\n",
        f"{indent}    return None\n",
        f"{indent}if direction == \"BUY\" and sl_price >= entry_price:\n",
        f"{indent}    log.error(f\"BLOCKED: BUY {{symbol}} SL {{sl_price}} >= Entry {{entry_price}}\")\n",
        f"{indent}    return None\n",
        f"{indent}if direction == \"SELL\" and sl_price <= entry_price:\n",
        f"{indent}    log.error(f\"BLOCKED: SELL {{symbol}} SL {{sl_price}} <= Entry {{entry_price}}\")\n",
        f"{indent}    return None\n",
        f"\n",
    ]
    
    for j, pl in enumerate(patch_lines):
        lines.insert(insert_before + j, pl)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    print("  executor.py gepatcht ✓")
    return True

if __name__ == "__main__":
    print("=== TP-Bug Fix v2 ===")
    ok1 = patch_agent()
    ok2 = patch_executor()
    if ok1 and ok2:
        print("\nFERTIG! Teste mit: python agent.py")
    else:
        print("\nFEHLER — prüfe Ausgabe oben")
