"""Download Hy-MT2-1.8B safetensors to D: (never C:).
Usage: HF_HOME=D:\\StoryTranslation_data\\hf python scripts/fetch_hymt.py
"""
import os
import sys

os.environ.setdefault("HF_HOME", r"D:\StoryTranslation_data\hf")

from huggingface_hub import snapshot_download  # noqa: E402

path = snapshot_download("tencent/Hy-MT2-1.8B")
print("downloaded to", path)
