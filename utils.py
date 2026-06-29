"""
HNS Agent 通用工具模块
存放公共函数、常量和类型定义
"""
import os
import sys
import json
from pathlib import Path
from typing import Any, Optional
from colorama import init, Fore, Style


# 初始化 colorama
init()


# 终端样式常量
class TerminalStyle:
    """终端样式管理器"""
    @staticmethod
    def style() -> dict[str, str]:
        """返回终端样式字典"""
        return {
            'ai': Fore.GREEN,
            'user': Fore.CYAN,
            'tool': Fore.YELLOW,
            'info': Fore.LIGHTBLUE_EX,
            'ok': Fore.LIGHTGREEN_EX,
            'warn': Fore.LIGHTYELLOW_EX,
            'err': Fore.LIGHTRED_EX + Style.BRIGHT,
            'dim': Style.DIM,
            'bright': Style.BRIGHT,
            'reset': Style.RESET_ALL,
        }

    @staticmethod
    def box(text: str, color: str, width: int = 60) -> str:
        """用分隔线和颜色包裹文本"""
        s = TerminalStyle.style()
        line = "─" * width
        c = color + s['bright'] if color != Fore.GREEN else color
        return f"  {c}{line}{s['reset']}\n  {color}{text}{s['reset']}\n  {c}{line}"


# 配置相关
CONFIG_DEFAULTS = {
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_MODEL": "deepseek-v4-flash",
    "MAX_SNAPSHOTS": 10,  # 最大保留快照数
    "MCP_SERVERS": "[]",  # MCP 服务器配置（JSON 数组格式）
}


def load_config(path: str = ".config") -> dict[str, str]:
    """
    加载配置文件

    Args:
        path: 配置文件路径

    Returns:
        配置字典
        
    优先级：配置文件 > 环境变量 > 默认值
    """
    config = dict(CONFIG_DEFAULTS)

    # 优先寻找可执行文件同目录下的配置
    config_path = None
    try:
        exe_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
        candidate = exe_dir / path
        if candidate.exists():
            config_path = candidate
    except Exception:
        pass

    # 其次寻找当前工作目录
    if config_path is None:
        cwd_candidate = Path.cwd() / path
        if cwd_candidate.exists():
            config_path = cwd_candidate

    # 加载配置文件（优先级最高）
    if config_path and config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            config.update(data)  # 配置文件覆盖默认值
            s = TerminalStyle.style()
            print(f"  {s['dim']}[+] 配置已加载: {list(data.keys())} (来源: {config_path}){s['reset']}")
        except Exception as e:
            s = TerminalStyle.style()
            print(f"  {s['warn']}[!] 配置文件解析失败: {e}{s['reset']}")

    # 环境变量作为备选（仅当配置文件中没有设置时才使用环境变量）
    for key in config:
        if key in os.environ and not config.get(key):
            config[key] = os.environ[key]

    return config


def validate_config(config: dict[str, str]) -> tuple[bool, list[str]]:
    """
    验证配置是否有效

    Args:
        config: 配置字典

    Returns:
        (是否有效, 错误列表)
    """
    errors = []

    if not config.get("ANTHROPIC_API_KEY"):
        errors.append("ANTHROPIC_API_KEY 未设置")

    try:
        max_snapshots = int(config.get("MAX_SNAPSHOTS", 10))
        if max_snapshots < 1:
            errors.append("MAX_SNAPSHOTS 必须 >= 1")
    except ValueError:
        errors.append("MAX_SNAPSHOTS 必须是整数")

    return len(errors) == 0, errors


def content_block_to_dict(block: Any) -> dict:
    """
    将 SDK 的 content block 对象转为普通 dict，确保序列化兼容

    Args:
        block: content block 对象

    Returns:
        字典表示
    """
    if isinstance(block, dict):
        return block

    # 尝试各种转换方法
    for method in ("model_dump", "dict", "to_dict"):
        fn = getattr(block, method, None)
        if callable(fn):
            return fn()

    # 兜底：手动提取已知字段
    result = {"type": getattr(block, "type", "text")}
    for attr in ("text", "name", "input", "id", "tool_use_id", "content", "source"):
        if hasattr(block, attr):
            result[attr] = getattr(block, attr)
    return result


def clean_old_snapshots(backup_dir: Path, max_snapshots: int = 10) -> int:
    """
    清理旧快照，只保留最近 N 个

    Args:
        backup_dir: 快照目录
        max_snapshots: 最大保留数量

    Returns:
        清理的快照数
    """
    if not backup_dir.exists():
        return 0

    snapshots = sorted(backup_dir.glob("long_term_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(snapshots) <= max_snapshots:
        return 0

    to_delete = snapshots[max_snapshots:]
    deleted = 0
    for snap in to_delete:
        try:
            snap.unlink()
            deleted += 1
        except Exception:
            pass

    return deleted


def ensure_directory(path: Path) -> Path:
    """
    确保目录存在，不存在则创建

    Args:
        path: 目录路径

    Returns:
        目录路径
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_json_loads(text: str, default: Any = None) -> Any:
    """
    安全加载 JSON

    Args:
        text: JSON 字符串
        default: 失败时的默认值

    Returns:
        解析结果
    """
    try:
        return json.loads(text)
    except Exception:
        return default


def extract_json(text: str) -> Optional[str]:
    """
    从文本中提取 JSON 内容

    Args:
        text: 包含 JSON 的文本

    Returns:
        JSON 字符串，如果未找到则返回 None
    """
    text = text.strip()

    # 尝试 ```json 包裹
    if "```json" in text:
        json_text = text.split("```json")[1].split("```")[0].strip()
        return json_text

    # 尝试 ``` 包裹
    if "```" in text:
        json_text = text.split("```")[1].split("```")[0].strip()
        return json_text

    # 直接尝试从 [ 或 { 开始
    if text.startswith(('[', '{')):
        return text

    return None
