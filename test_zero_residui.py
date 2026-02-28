"""Test v5.30: grep zero residui separatori in csuite/ e command_center."""
import os
import re

PATTERNS = [
    r"━━━", r"───", r"___", r"===", r"---",
    r"risponde:", r"CTO:", r"CMO:", r"CFO:", r"CSO:", r"COO:", r"👤",
]

def scan_file(filepath, patterns):
    """Scansiona un file per pattern vietati. Restituisce lista di match."""
    matches = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            for pat in patterns:
                if pat in line:
                    matches.append((filepath, i, pat, line.strip()[:80]))
    return matches

def test_csuite_zero_residui():
    """Nessun pattern vietato in csuite/."""
    csuite_dir = os.path.join("deploy-agents", "csuite")
    all_matches = []
    for fname in os.listdir(csuite_dir):
        if fname.endswith(".py"):
            fpath = os.path.join(csuite_dir, fname)
            all_matches.extend(scan_file(fpath, PATTERNS))
    if all_matches:
        for filepath, line, pat, text in all_matches:
            print(f"  FAIL: {filepath}:{line} pattern '{pat}' -> {text}")
    assert len(all_matches) == 0, f"{len(all_matches)} residui trovati in csuite/"
    print("PASS: csuite/ zero residui separatori")

def test_command_center_zero_residui():
    """Nessun pattern vietato in command_center_unified.py."""
    fpath = os.path.join("deploy", "command_center_unified.py")
    matches = scan_file(fpath, PATTERNS)
    if matches:
        for filepath, line, pat, text in matches:
            print(f"  FAIL: {filepath}:{line} pattern '{pat}' -> {text}")
    assert len(matches) == 0, f"{len(matches)} residui trovati in command_center"
    print("PASS: command_center zero residui separatori")

if __name__ == "__main__":
    test_csuite_zero_residui()
    test_command_center_zero_residui()
    print("\nTutti i test PASS")
