"""Find the biggest safe-to-clean C: hogs (report only, no deletes).
Usage: python scripts/find_c_space.py
"""
import os
from pathlib import Path

home = Path.home()
local = Path(os.environ.get("LOCALAPPDATA", ""))
cands = {
    "local_temp": local / "Temp",
    "pip_cache": local / "pip" / "cache",
    "hf_cache_c": home / ".cache" / "huggingface",
    "opencode_tool_output": Path(r"C:\Users\shash\.local\share\opencode\tool-output"),
    "opencode_repos": Path(r"C:\Users\shash\.local\share\opencode\repos"),
    "npm_cache": local / "npm-cache",
    "vscode_backup": home / "AppData" / "Roaming" / "Code" / "Backups",
    "deliver_opt": Path(r"C:\Windows\SoftwareDistribution\DeliveryOptimization"),
}


def du(path: Path, depth_budget: int = 400000) -> tuple[float, int]:
    total, n = 0, 0
    try:
        for p in path.rglob("*"):
            n += 1
            if n > depth_budget:
                break
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError as e:
        print(f"  !! {e}")
    return total / 2**30, n


for name, path in cands.items():
    if path.exists():
        gb, n = du(path)
        print(f"{name}: {gb:.2f}G over ~{n} files  [{path}]")
    else:
        print(f"{name}: MISSING")
