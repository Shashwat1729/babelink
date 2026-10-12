"""Locate the Ollama model store. Usage: python scripts/find_ollama.py"""
import os
from pathlib import Path

print("OLLAMA_MODELS =", os.environ.get("OLLAMA_MODELS", "(unset)"))
print("OLLAMA_HOST =", os.environ.get("OLLAMA_HOST", "(unset)"))


def du(path: Path) -> float:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total / 2**30


cands = [
    Path.home() / ".ollama",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama",
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Ollama",
    Path(r"C:\Program Files\Ollama"),
]
for c in cands:
    print(f"{c} -> {'MISSING' if not c.exists() else f'{du(c):.2f}G'}")
