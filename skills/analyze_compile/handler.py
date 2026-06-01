from pathlib import Path


def handle_analyze_compile(action=""):
    """分析编译日志，查找 compile.log 并分析错误"""
    log_paths = [
        Path("compile.log"),
        Path("../compile.log"),
        Path("../../compile.log"),
        Path("F:/HNS/compile.log"),
    ]
    for p in log_paths:
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            errors = [l for l in lines if "error" in l.lower() or "Error" in l or "ERROR" in l]
            warnings = [l for l in lines if "warning" in l.lower() or "Warning" in l or "WARNING" in l]
            result = [f"📄 找到编译日志: {p}"]
            result.append(f"总行数: {len(lines)}, 错误: {len(errors)}, 警告: {len(warnings)}\n")
            if errors:
                result.append("🔴 错误信息:")
                result.extend(errors[:20])
            if warnings:
                result.append("\n🟡 警告信息:")
                result.extend(warnings[:20])
            return "\n".join(result)
    return "[错误] 未找到 compile.log 文件"