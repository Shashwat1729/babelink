"""Print free space for C: and D:. Usage: python scripts/disk.py"""
import shutil

for drive in ("C:\\", "D:\\"):
    try:
        total, used, free = shutil.disk_usage(drive)
        print(f"{drive} total={total/2**30:.1f}G used={used/2**30:.1f}G free={free/2**30:.1f}G")
    except OSError as e:
        print(f"{drive} unavailable: {e}")
