import shutil
from pathlib import Path


def handle_fix_undefined_vars(action=""):
    """将 libGD32F303VET6.a.bk 复制覆盖为 libGD32F303VET6.a"""
    src = Path("libGD32F303VET6.a.bk")
    dst = Path("libGD32F303VET6.a")
    if not src.exists():
        return f"[错误] 备份文件不存在: {src}"
    try:
        shutil.copy2(src, dst)
        return f"已复制覆盖: {src} → {dst}"
    except Exception as e:
        return f"[错误] 复制失败: {e}"