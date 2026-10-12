"""Report RAM + sizes of known disk hogs. Usage: python scripts/sysprobe.py"""
import os
import shutil
from pathlib import Path

try:
    import psutil  # type: ignore
    vm = psutil.virtual_memory()
    print(f"RAM total={vm.total/2**30:.1f}G avail={vm.available/2**30:.1f}G used={vm.percent}%")
except ImportError:
    print("psutil not installed; RAM unknown")


def du(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError as e:
        print(f"  !! {path}: {e}")
    return total


home = Path.home()
cands = [
    home / ".ollama" / "models",
    home / ".cache" / "huggingface",
    Path(r"D:\cache\huggingface"),
    Path(os.environ.get("HF_HOME", "")) if os.environ.get("HF_HOME") else None,
    home / "AppData" / "Local" / "pip" / "cache",
    home / "AppData" / "Local" / "Temp",
    Path(r"D:\personal\projects\Story-translation\eval_runs"),
    Path(r"D:\personal\projects\Story-translation\corpus"),
]
for c in cands:
    if c is None:
        continue
    if c.exists():
        print(f"{c} -> {du(c)/2**30:.2f}G")
    else:
        print(f"{c} -> MISSING")
