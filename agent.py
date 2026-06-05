"""
Denny Agent - 最精简智能体
支持：本地工具 / MCP 工具 / Skills / LLM 对话 / 记忆系统
"""
import os
import sys
import json
import subprocess
import time
import asyncio
import shutil
import tempfile
import re
from typing import Optional, Any, Callable, Dict, List
from pathlib import Path
from datetime import datetime, timedelta

def sanitize_emoji(text: str) -> str:
    """移除文本中的表情符号，防止 Windows 终端编码错误"""
    emoji_pattern = re.compile(
        r'[\U00010000-\U0010ffff]|[\u2600-\u26FF]|[\u2700-\u27BF]|[\u203C-\u2049]'
    )
    return emoji_pattern.sub('', text)

from anthropic import AsyncAnthropic
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from utils import (
    TerminalStyle,
    load_config,
    validate_config,
    content_block_to_dict,
    clean_old_snapshots,
    ensure_directory,
    safe_json_loads,
    extract_json,
)

# Swarm 多Agent协作（延迟导入，避免循环依赖）
# from swarm_agent import OrchestratorAgent  # 在 chat() 中按需导入


# ── 加载 .config 配置文件 ──────────────────────────────
config = load_config()
# 无论验证是否通过，都设置环境变量（验证只是给出警告）
for key, value in config.items():
    if value:  # 只设置非空值
        os.environ[key] = str(value)

is_valid, config_errors = validate_config(config)
if not is_valid:
    s = TerminalStyle.style()
    print(f"  {s['warn']}[!] 配置警告: {', '.join(config_errors)}{s['reset']}")


# ═══════════════════════════════════════════════════════
#  操作日志 - Operation Logger
# ═══════════════════════════════════════════════════════

class OperationLog:
    """
    每一次操作的日志记录器。
    写入 .agent_memory/operations/ 目录，按日期分文件。
    """
    def __init__(self, storage_path: str = ".agent_memory"):
        self.log_dir = Path(storage_path) / "operations"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._today = None
        self._handle = None

    def _get_file(self):
        """每天一个日志文件"""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today:
            if self._handle:
                self._handle.close()
            self._today = today
            log_file = self.log_dir / f"operations_{today}.log"
            self._handle = open(log_file, "a", encoding="utf-8")
        return self._handle

    def _write(self, level: str, message: str):
        """写入一行日志"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fh = self._get_file()
        fh.write(f"[{ts}] [{level}] {message}\n")
        fh.flush()

    def user_input(self, text: str):
        """记录用户输入"""
        self._write("USER", text)

    def ai_response(self, text: str, cost: str = ""):
        """记录 AI 回复"""
        preview = text[:200].replace("\n", "\\n")
        if len(text) > 200:
            preview += "..."
        self._write("AI", f"{preview} {cost}")

    def tool_call(self, name: str, args: dict, result: str):
        """记录工具调用"""
        args_str = json.dumps(args, ensure_ascii=False)[:300]
        result_preview = str(result)[:200].replace("\n", "\\n")
        if len(str(result)) > 200:
            result_preview += "..."
        self._write("TOOL", f"{name}({args_str}) → {result_preview}")

    def command(self, cmd: str, detail: str = ""):
        """记录命令操作"""
        self._write("CMD", f"{cmd} {detail}")

    def info(self, message: str):
        """记录系统信息"""
        self._write("INFO", message)

    def error(self, message: str):
        """记录错误"""
        self._write("ERROR", message)

    def swarm(self, goal: str, result: str):
        """记录 Swarm 多Agent协作"""
        result_preview = result[:300].replace("\n", "\\n")
        if len(result) > 300:
            result_preview += "..."
        self._write("SWARM", f"目标: {goal} → {result_preview}")

    def close(self):
        """关闭日志文件"""
        if self._handle:
            self._handle.close()
            self._handle = None

class Memory:
    """
    三层记忆系统：
    1. 短期记忆 (Short-term): 当前会话的对话历史，受上下文窗口限制
    2. 长期记忆 (Long-term): 跨会话持久化的关键事实、偏好、知识
    3. 情景记忆 (Episodic): 最近对话的摘要，用于上下文回溯
    """

    def __init__(self, storage_path: str = ".agent_memory", max_snapshots: int = 10):
        self.storage_path = Path(storage_path)
        self.backup_dir = self.storage_path / "backups"
        self.short_term: List[Dict[str, Any]] = []  # 当前对话轮次
        self.episodic: List[Dict[str, Any]] = []    # 已结束的对话摘要
        self.long_term = {  # 持久化事实
            "facts": [],       # 提取的事实
            "preferences": [], # 用户偏好
            "knowledge": {},   # 领域知识
            "created_at": None,
            "updated_at": None,
        }
        self.max_snapshots = max_snapshots
        self._load_long_term()

    def _load_long_term(self):
        """加载长期记忆"""
        # 确保目录存在
        ensure_directory(self.storage_path)
        ensure_directory(self.backup_dir)

        mem_file = self.storage_path / "long_term.json"
        if mem_file.exists():
            try:
                data = safe_json_loads(mem_file.read_text(encoding="utf-8"), {})
                if isinstance(data, dict):
                    self.long_term.update(data)
                    s = TerminalStyle.style()
                    print(f"  {s['dim']}[记忆] 加载长期记忆: {len(self.long_term.get('facts', []))} 条事实, "
                          f"{len(self.long_term.get('preferences', []))} 条偏好{s['reset']}")
            except Exception as e:
                s = TerminalStyle.style()
                print(f"  {s['warn']}[!] 长期记忆加载失败: {e}{s['reset']}")

        # 初始化时间戳（仅首次创建时）
        now = datetime.now().isoformat()
        if self.long_term.get("created_at") is None:
            self.long_term["created_at"] = now
        if self.long_term.get("updated_at") is None:
            self.long_term["updated_at"] = now

        # 保存前备份当前文件
        if mem_file.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_dir / f"long_term_{ts}.json"
            backup_file.write_text(mem_file.read_text(encoding="utf-8"), encoding="utf-8")
            # 清理旧快照
            deleted = clean_old_snapshots(self.backup_dir, self.max_snapshots)
            if deleted > 0:
                s = TerminalStyle.style()
                print(f"  {s['dim']}[记忆] 清理了 {deleted} 个旧快照{s['reset']}")
        self._save_long_term()

    def _save_long_term(self):
        """保存长期记忆到磁盘"""
        mem_file = self.storage_path / "long_term.json"
        ensure_directory(mem_file.parent)
        self.long_term["updated_at"] = datetime.now().isoformat()
        mem_file.write_text(json.dumps(self.long_term, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_backups(self) -> List[Dict[str, Any]]:
        """列出所有快照备份（按时间倒序）"""
        if not self.backup_dir.exists():
            return []
        backups = []
        for f in sorted(self.backup_dir.glob("long_term_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = safe_json_loads(f.read_text(encoding="utf-8"), {})
                if isinstance(data, dict):
                    facts = len(data.get("facts", []))
                    prefs = len(data.get("preferences", []))
                    updated = data.get("updated_at", "?")
                    backups.append({
                        "filename": f.name,
                        "time": updated,
                        "facts": facts,
                        "preferences": prefs,
                        "data": data,
                    })
            except Exception:
                continue
        return backups

    def rollback(self, index: int) -> Dict[str, Any]:
        """
        回退到指定索引的快照，恢复后清空短期记忆并重载。
        返回新 long_term 的状态。
        """
        backups = self._load_backups()
        if index < 0 or index >= len(backups):
            raise ValueError(f"索引 {index} 无效，有效范围 0-{len(backups)-1}")
        snap = backups[index]
        self.long_term = snap["data"]
        self.long_term["updated_at"] = datetime.now().isoformat()
        self.short_term.clear()
        self._save_long_term()
        return self.get_status()

    def add_to_short_term(self, role: str, content: Any):
        """添加短期记忆（对话历史）"""
        self.short_term.append({"role": role, "content": content})

    async def summarize_recent(self, messages: List[Dict[str, Any]], llm_client, model: str) -> str:
        """
        使用 LLM 对最近对话进行摘要，释放上下文空间
        返回摘要文本，用于压缩历史
        """
        try:
            resp = await llm_client.messages.create(
                model=model,
                messages=[
                    {"role": "user", "content": (
                        "请将以下对话压缩为一段简洁的摘要（不超过100字），"
                        "保留关键信息和上下文线索：\n\n"
                        + "\n".join(
                            f"{m.get('role', 'unknown')}: {str(m.get('content', ''))[:200]}"
                            for m in messages[-10:]
                        )
                    )}
                ],
                max_tokens=500,
            )
            text_blocks = [b.text for b in resp.content if hasattr(b, 'text')]
            summary = text_blocks[0] if text_blocks else "(摘要生成失败)"
            self.episodic.append({
                "timestamp": datetime.now().isoformat(),
                "summary": summary,
                "message_count": len(messages),
            })
            # 只保留最近5条摘要
            self.episodic = self.episodic[-5:]
            return summary
        except asyncio.CancelledError:
            raise
        except Exception as e:
            s = TerminalStyle.style()
            print(f"  {s['warn']}[!] 摘要生成失败: {e}{s['reset']}")
            return ""

    async def extract_facts(self, messages: List[Dict[str, Any]], llm_client, model: str) -> List[Dict[str, Any]]:
        """从对话中提取重要事实和偏好，存入长期记忆"""
        try:
            resp = await llm_client.messages.create(
                model=model,
                messages=[
                    {"role": "user", "content": (
                        "请从以下对话中提取重要的事实、用户偏好或知识。"
                        "如果存在新信息，以 JSON 数组格式返回，每项包含 type(fact/preference) 和 content 字段。\n"
                        "如果没有值得记忆的新信息，返回空数组 []。\n\n"
                        + "\n".join(
                            f"{m.get('role', 'unknown')}: {str(m.get('content', ''))[:300]}"
                            for m in messages[-15:]
                        )
                    )}
                ],
                max_tokens=1000,
            )
            text_blocks = [b.text for b in resp.content if hasattr(b, 'text')]
            text = text_blocks[0] if text_blocks else "[]"
            # 尝试解析 JSON
            json_text = extract_json(text)
            if json_text:
                items = safe_json_loads(json_text, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "content" in item:
                            key = item.get("type", "fact")
                            content_val = item["content"]
                            if key == "preference" and content_val not in self.long_term["preferences"]:
                                self.long_term["preferences"].append(content_val)
                            elif key == "fact" and content_val not in self.long_term["facts"]:
                                self.long_term["facts"].append(content_val)
                            else:
                                # 视为一般知识
                                self.long_term["knowledge"][content_val] = True
                    return items
        except asyncio.CancelledError:
            raise
        except Exception as e:
            s = TerminalStyle.style()
            print(f"  {s['warn']}[!] 事实提取失败: {e}{s['reset']}")
        return []

    def get_long_term_context(self) -> str:
        """生成长期记忆的上下文文本，供系统提示使用"""
        parts = []
        if self.long_term.get("facts"):
            parts.append("【已知事实】\n" + "\n".join(f"• {f}" for f in self.long_term["facts"][-20:]))
        if self.long_term.get("preferences"):
            parts.append("【用户偏好】\n" + "\n".join(f"• {p}" for p in self.long_term["preferences"][-10:]))
        if self.episodic:
            parts.append("【近期对话回顾】\n" + "\n".join(
                f"- [{e['timestamp'][:10]}] {e['summary']}" for e in self.episodic[-3:]
            ))
        return "\n".join(parts) if parts else ""

    def clear_short_term(self):
        """清空短期记忆"""
        self.short_term.clear()

    def get_status(self) -> Dict[str, Any]:
        """获取记忆状态"""
        return {
            "short_term_messages": len(self.short_term),
            "episodic_summaries": len(self.episodic),
            "long_term_facts": len(self.long_term.get("facts", [])),
            "long_term_preferences": len(self.long_term.get("preferences", [])),
        }


# ── 内置工具定义 ────────────────────────────────────────
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "搜索互联网获取实时信息",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_file",
        "description": "读取指定路径文件的内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "创建或覆盖写入文件",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "run_command",
        "description": "在本地终端执行 shell 命令",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "list_dir",
        "description": "列出目录内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认为当前目录"}
            },
            "required": []
        }
    },
    {
        "name": "calc",
        "description": "执行数学计算，支持复杂表达式",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式，如 '2 ** 10'"}
            },
            "required": ["expression"]
        }
    },
    {
        "name": "read_image",
        "description": "读取图片文件，返回尺寸、格式、文件大小等元信息",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "图片文件路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "open_file",
        "description": "用系统默认程序打开文件（图片/文档/网页等）",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要打开的文件路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "image_process",
        "description": "图片处理：创建/编辑/抠图/转换格式/改变大小/裁剪/旋转/翻转/调整亮度对比度",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作: create(创建), resize(调整大小), convert(转换格式), remove_bg(抠图/去背景), crop(裁剪), rotate(旋转), flip(翻转), adjust(亮度对比度)"
                },
                "input": {"type": "string", "description": "输入图片路径(create 时不需要)"},
                "output": {"type": "string", "description": "输出图片路径"},
                "width": {"type": "integer", "description": "宽度(create/resize)"},
                "height": {"type": "integer", "description": "高度(create/resize)"},
                "format": {"type": "string", "description": "目标格式: png, jpg, webp, bmp, gif(convert/create)"},
                "color": {"type": "string", "description": "背景色(create), 如 'red', '#FF0000'"},
                "left": {"type": "integer", "description": "裁剪左边界(crop)"},
                "top": {"type": "integer", "description": "裁剪上边界(crop)"},
                "right": {"type": "integer", "description": "裁剪右边界(crop)"},
                "bottom": {"type": "integer", "description": "裁剪下边界(crop)"},
                "angle": {"type": "number", "description": "旋转角度(rotate), 如 90, -45"},
                "direction": {"type": "string", "description": "翻转方向(flip): horizontal 或 vertical"},
                "brightness": {"type": "number", "description": "亮度调整(adjust), 1.0=原值, >1 增亮"},
                "contrast": {"type": "number", "description": "对比度调整(adjust), 1.0=原值, >1 增强"}
            },
            "required": ["action", "output"]
        }
    },
    {
        "name": "edit_file",
        "description": "在文件中执行精确的字符串替换（查找并替换）。old_string 必须在文件中唯一匹配，否则操作失败。适用于对现有文件做定向修改，比 write_file 更安全。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要编辑的文件路径"},
                "old_string": {"type": "string", "description": "要替换的原始文本（必须在文件中唯一存在）"},
                "new_string": {"type": "string", "description": "替换后的新文本"},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配项（默认 false，仅替换唯一匹配）", "default": False}
            },
            "required": ["path", "old_string", "new_string"]
        }
    },
    {
        "name": "search_code",
        "description": "在文件中搜索匹配正则表达式的内容，类似 grep。返回匹配的文件路径、行号和内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索的正则表达式"},
                "path": {"type": "string", "description": "搜索目录或文件路径，默认为当前目录"},
                "glob": {"type": "string", "description": "文件名过滤 glob 模式，如 '*.py' 或 '*.{js,ts}'"},
                "max_results": {"type": "integer", "description": "最大返回结果数（默认 30）", "default": 30}
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "find_files",
        "description": "用 glob 模式查找匹配的文件，返回排序后的文件路径列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 匹配模式，如 '**/*.py'、'src/**/*.ts'"},
                "path": {"type": "string", "description": "搜索的根目录，默认为当前目录"}
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "run_code",
        "description": "编写并运行代码。支持 Python, C, JavaScript, TypeScript, Java, Go。自动创建临时文件、编译（如需）、执行，返回 stdout/stderr。超时 30 秒。",
        "input_schema": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "编程语言: python, c, javascript (或 js), typescript (或 ts), java, go"
                },
                "code": {
                    "type": "string",
                    "description": "完整的源代码"
                },
                "filename": {
                    "type": "string",
                    "description": "可选，自定义文件名（含扩展名）。不指定则自动生成"
                }
            },
            "required": ["language", "code"]
        }
    },
    {
        "name": "embedded_doc",
        "description": "查询嵌入式固件函数说明。根据函数名或功能关键词，返回对应函数的声明、参数说明和简要用途（不执行，只查询文档）。用于查调MCU固件中的C函数接口。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "函数名（如 PowerOn_Process）或功能关键词（如 power, key, init）"
                }
            },
            "required": ["query"]
        }
    },
]


# ── 工具函数 ────────────────────────────────────────────
# 已移至 utils.py: content_block_to_dict
# 保留别名保持兼容性
def _content_block_to_dict(block: Any) -> Dict[str, Any]:
    """将 SDK 的 content block 对象转为普通 dict，确保序列化兼容"""
    return content_block_to_dict(block)


# ── 工具执行函数 ────────────────────────────────────────
TOOL_HANDLERS = {}

def _register(name):
    def decorator(fn):
        TOOL_HANDLERS[name] = fn
        return fn
    return decorator


@_register("web_search")
async def handle_web_search(q):
    """多引擎搜索：Bing(国内) → DuckDuckGo(国外) 自动备选"""
    import urllib.request, urllib.parse
    import ssl
    import asyncio

    # 清理 SSL 上下文，避免部分站点证书问题
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36"
    }
    q_enc = urllib.parse.quote(q)

    def _fetch(url):
        req = urllib.request.Request(url, headers=headers)
        r = urllib.request.urlopen(req, timeout=15, context=ssl_ctx)
        return r.read().decode("utf-8", errors="replace")

    engines = [
        # 引擎1: Bing (国内可访问)
        {
            "name": "Bing",
            "url": f"https://cn.bing.com/search?q={q_enc}",
            "parse": lambda html: _extract_bing(html),
        },
        # 引擎2: DuckDuckGo (国外备选)
        {
            "name": "DuckDuckGo",
            "url": f"https://html.duckduckgo.com/html/?q={q_enc}",
            "parse": lambda html: _extract_ddg(html),
        },
    ]

    # 用 run_in_executor 避免阻塞事件循环
    loop = asyncio.get_event_loop()

    for engine in engines:
        try:
            if engine["name"] == "DuckDuckGo":
                # 给 DuckDuckGo 额外 2 秒 (国内可能更慢)
                pass
            html = await loop.run_in_executor(None, _fetch, engine["url"])
            result = engine["parse"](html)
            if len(result) > 50:  # 有实质内容才返回
                return f" [{engine['name']}] 搜索结果：\n\n{result[:3000]}"
        except Exception as e:
            continue  # 引擎失败，自动切到下一个

    # 所有引擎都失败
    # 最后一次尝试：直接返回 DuckDuckGo 原始错误信息
    try:
        html = await loop.run_in_executor(None, _fetch, engines[-1]["url"])
        return _extract_ddg(html)[:3000]
    except Exception as e:
        return f"搜索失败（所有引擎均不可用）: {e}"


def _extract_bing(html):
    """从 Bing 搜索结果页提取标题+摘要"""
    import re
    results = []
    # Bing 搜索结果条目
    items = re.findall(
        r'<li class="b_algo">.*?<h2><a[^>]*href="([^"]*)"[^>]*>(.*?)</a></h2>'
        r'.*?<p[^>]*>(.*?)</p>',
        html, re.DOTALL
    )
    for url, title, snippet in items[:8]:
        clean_title = re.sub(r'<[^>]+>', '', title).strip()
        clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
        results.append(f"  • {clean_title}\n    {clean_snippet}\n    {url}")
    if results:
        return "\n\n".join(results)
    # 备选: 提取所有 <a> 标签
    fallback = re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    lines = []
    for url, title in fallback[:10]:
        t = re.sub(r'<[^>]+>', '', title).strip()
        if t:
            lines.append(f"  • {t}\n    {url}")
    return "\n\n".join(lines) if lines else html[:2000]


def _extract_ddg(html):
    """从 DuckDuckGo 搜索结果页提取标题+摘要"""
    import re
    results = re.findall(
        r'<a rel="nofollow" class="result__a" href="([^"]*)".*?>(.*?)</a>'
        r'.*?<a class="result__snippet".*?>(.*?)</a>',
        html, re.DOTALL
    )
    lines = []
    for url, title, snippet in results[:8]:
        clean_title = re.sub(r'<[^>]+>', '', title).strip()
        clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
        lines.append(f"  • {clean_title}\n    {clean_snippet}\n    {url}")
    if lines:
        return "\n\n".join(lines)
    # 备选: 原始 HTML 截取
    return html[:2000]


@_register("read_file")
def handle_read_file(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:3000]
    except Exception as e:
        return f"读取失败: {e}"


@_register("write_file")
def handle_write_file(path, content):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        return f"已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"写入失败: {e}"


@_register("open_file")
def handle_open_file(path):
    try:
        p = Path(path)
        if not p.exists():
            return f"[错误] 文件不存在: {path}"
        if os.name == "nt":
            os.startfile(str(p.resolve()))
        elif os.uname().sysname == "Darwin":
            subprocess.run(["open", str(p.resolve())], check=True)
        else:
            subprocess.run(["xdg-open", str(p.resolve())], check=True)
        return f"已打开: {path}"
    except Exception as e:
        return f"打开失败: {e}"




@_register("run_command")
def handle_run_command(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=str(Path.cwd()))
        return r.stdout + r.stderr or "(无输出)"
    except subprocess.TimeoutExpired:
        return "命令超时 (30s)"
    except Exception as e:
        return f"执行错误: {e}"


@_register("list_dir")
def handle_list_dir(path="."):
    try:
        entries = []
        for f in Path(path).iterdir()[:50]:
            kind = "dir" if f.is_dir() else "file"
            entries.append(f"  [{kind}] {f.name}")
        return "\n".join(entries) or f"(空目录)"
    except Exception as e:
        return f"读取失败: {e}"


@_register("calc")
def handle_calc(expression):
    # 仅允许安全的数学运算
    allowed = set("0123456789+-*/.() %")
    if not all(c in allowed for c in expression.replace(" ", "")):
        return "[错误] 非法字符，仅允许数字和 + - * / . ( ) %"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"[计算错误] {e}"


@_register("read_image")
def handle_read_image(path):
    try:
        from PIL import Image
    except ImportError:
        return "[错误] 未安装 Pillow 库，请执行: pip install Pillow"

    try:
        img = Image.open(path)
        file_size = Path(path).stat().st_size
        info = {
            "路径": path,
            "格式": img.format or "未知",
            "模式": img.mode,
            "尺寸": f"{img.width} × {img.height} 像素",
            "文件大小": f"{file_size:,} 字节 ({file_size / 1024:.1f} KB)",
        }
        lines = [f"{k}: {v}" for k, v in info.items()]
        return "\n".join(lines)
    except Exception as e:
        return f"读取图片失败: {e}"


@_register("image_process")
def handle_image_process(action, output, input=None, width=None, height=None,
                         format=None, color=None, left=None, top=None,
                         right=None, bottom=None, angle=None, direction=None,
                         brightness=None, contrast=None):
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return "[错误] 未安装 Pillow 库，请执行: pip install Pillow"

    try:
        out_path = Path(output)

        # ── create: 创建纯色画布 ──
        if action == "create":
            if not width or not height:
                return "[错误] create 需要 width 和 height 参数"
            bg = str(color or "white")
            try:
                img = Image.new("RGB", (int(width), int(height)), bg)
            except Exception:
                img = Image.new("RGB", (int(width), int(height)), "white")
            fmt = format or out_path.suffix.lstrip(".") or "png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, format=fmt.upper() if fmt.upper() != "JPG" else "JPEG")
            return f"已创建: {output} ({width}×{height}, {fmt})"

        # ── 其他操作需要 input ──
        if not input:
            return "[错误] 需要 input 输入图片路径"
        img = Image.open(input)

        if action == "resize":
            if not width and not height:
                return "[错误] resize 需要 width 或 height"
            w = int(width) if width else int(img.width * (int(height) / img.height))
            h = int(height) if height else int(img.height * (int(width) / img.width))
            img = img.resize((w, h), Image.LANCZOS)

        elif action == "convert":
            fmt = format or out_path.suffix.lstrip(".") or "png"
            if img.mode in ("RGBA", "P") and fmt.upper() in ("JPEG", "JPG"):
                img = img.convert("RGB")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, format=fmt.upper() if fmt.upper() != "JPG" else "JPEG")
            return f"已转换: {input} → {output} ({fmt.upper()}, {img.width}×{img.height})"

        elif action == "remove_bg":
            try:
                from rembg import remove
            except ImportError:
                return "[错误] 未安装 rembg，请执行: pip install rembg"
            img = remove(img)
            # 输出默认为 PNG（保留透明通道）

        elif action == "crop":
            if left is None or top is None or right is None or bottom is None:
                return "[错误] crop 需要 left, top, right, bottom 参数"
            img = img.crop((int(left), int(top), int(right), int(bottom)))

        elif action == "rotate":
            if angle is None:
                return "[错误] rotate 需要 angle 参数"
            # expand=True 防止旋转后裁切
            img = img.rotate(float(angle), expand=True, resample=Image.BICUBIC)

        elif action == "flip":
            if direction == "horizontal":
                img = ImageOps.mirror(img)
            elif direction == "vertical":
                img = ImageOps.flip(img)
            else:
                return "[错误] flip 的 direction 必须是 horizontal 或 vertical"

        elif action == "adjust":
            changed = False
            if brightness is not None:
                img = ImageEnhance.Brightness(img).enhance(float(brightness))
                changed = True
            if contrast is not None:
                img = ImageEnhance.Contrast(img).enhance(float(contrast))
                changed = True
            if not changed:
                return "[错误] adjust 需要 brightness 或 contrast 参数"

        else:
            return f"[错误] 未知操作: {action}，支持: create/resize/convert/remove_bg/crop/rotate/flip/adjust"

        # 保存输出
        fmt = format or out_path.suffix.lstrip(".") or img.format or "png"
        if fmt.upper() in ("JPEG", "JPG") and img.mode == "RGBA":
            img = img.convert("RGB")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format=fmt.upper() if fmt.upper() != "JPG" else "JPEG")
        return f"已处理: {output} ({img.width}×{img.height}, 操作: {action})"

    except Exception as e:
        return f"图片处理失败: {e}"


@_register("edit_file")
def handle_edit_file(path, old_string, new_string, replace_all=False):
    try:
        p = Path(path)
        if not p.exists():
            return f"[错误] 文件不存在: {path}"
        content = p.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return f"[错误] 未找到匹配文本"
        if not replace_all and count > 1:
            return f"[错误] 找到 {count} 处匹配，old_string 不唯一。请扩大上下文使其唯一，或设置 replace_all=true"
        new_content = content.replace(old_string, new_string)
        p.write_text(new_content, encoding="utf-8")
        return f"已完成: 替换了 {count} 处匹配 → {path}"
    except Exception as e:
        return f"编辑失败: {e}"


@_register("search_code")
def handle_search_code(pattern, path=".", glob=None, max_results=30):
    import re
    try:
        base = Path(path)
        if not base.exists():
            return f"[错误] 路径不存在: {path}"
        if base.is_file():
            files = [base]
        else:
            files = list(base.rglob("*" if glob is None else glob))
            files = [f for f in files if f.is_file() and f.suffix not in {".exe", ".dll", ".so", ".bin", ".pyc"}]
        if not files:
            return "(未找到匹配的文件)"
        results = []
        regex = re.compile(pattern)
        for f in files:
            if len(results) >= max_results:
                break
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if len(results) >= max_results:
                        break
                    if regex.search(line):
                        results.append(f"{f}:{i}: {line.strip()[:200]}")
            except Exception:
                continue
        return "\n".join(results) if results else f"(未找到匹配: {pattern})"
    except re.error as e:
        return f"[正则错误] {e}"
    except Exception as e:
        return f"搜索失败: {e}"


@_register("find_files")
def handle_find_files(pattern, path="."):
    try:
        base = Path(path)
        if not base.exists():
            return f"[错误] 路径不存在: {path}"
        matches = sorted(base.glob(pattern))
        if not matches:
            return f"(未找到匹配: {pattern})"
        lines = []
        for m in matches[:50]:
            kind = "dir" if m.is_dir() else "file"
            size = ""
            if m.is_file():
                try:
                    s = m.stat().st_size
                    if s < 1024:
                        size = f" ({s}B)"
                    elif s < 1024 * 1024:
                        size = f" ({s/1024:.1f}KB)"
                    else:
                        size = f" ({s/1024/1024:.1f}MB)"
                except Exception:
                    pass
            lines.append(f"[{kind}] {m}{size}")
        return "\n".join(lines)
    except Exception as e:
        return f"查找失败: {e}"


LANG_CONFIG = {
    "python":      {"ext": ".py",  "run": lambda f: ["python", f]},
    "c":           {"ext": ".c",   "compile": lambda f: ["gcc", f, "-o", f[:-2] + ".exe", "-Wall"], "bin": lambda f: [f[:-2] + ".exe"]},
    "javascript":  {"ext": ".js",  "run": lambda f: ["node", f]},
    "js":          {"ext": ".js",  "run": lambda f: ["node", f]},
    "typescript":  {"ext": ".ts",  "run": lambda f: ["npx", "tsx", f]},
    "ts":          {"ext": ".ts",  "run": lambda f: ["npx", "tsx", f]},
    "java":        {"ext": ".java", "compile": lambda f: ["javac", f], "run": lambda f, cls: ["java", "-cp", str(Path(f).parent), cls]},
    "go":          {"ext": ".go",  "run": lambda f: ["go", "run", f]},
}


@_register("run_code")
def handle_run_code(language, code, filename=None):
    lang = language.lower().strip()
    cfg = LANG_CONFIG.get(lang)
    if not cfg:
        return f"[错误] 不支持的语言: {language}，支持: {', '.join(LANG_CONFIG.keys())}"

    ext = cfg["ext"]
    fname = filename if filename else f"_code_{int(time.time())}{ext}"
    tmpdir = tempfile.mkdtemp(prefix="agent_code_")
    filepath = str(Path(tmpdir) / fname)

    try:
        Path(filepath).write_text(code, encoding="utf-8")
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return f"[错误] 写入文件失败: {e}"

    try:
        # 编译（如果需要）
        if "compile" in cfg:
            compile_cmd = cfg["compile"](filepath)
            r = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=30, cwd=tmpdir)
            if r.returncode != 0:
                return f"[编译失败]\n{' '.join(compile_cmd)}\n\n{r.stderr}"

        # 运行
        if lang == "java":
            cls = Path(fname).stem
            run_cmd = cfg["run"](filepath, cls)
        else:
            run_cmd = cfg["run"](filepath)

        r = subprocess.run(run_cmd, capture_output=True, text=True, timeout=30, cwd=tmpdir)
        output = []
        if r.stdout:
            output.append(r.stdout.strip())
        if r.stderr:
            output.append(f"[stderr]\n{r.stderr.strip()}")
        return "\n\n".join(output) or "(无输出，进程退出码: {})".format(r.returncode)

    except subprocess.TimeoutExpired:
        return "[超时] 代码执行超过 30 秒"
    except FileNotFoundError as e:
        tool = "gcc" if lang == "c" else ("javac/java" if lang == "java" else ("tsx" if lang in ("typescript","ts") else lang))
        return f"[错误] 未找到 {tool}，请确认已安装并加入 PATH"
    except Exception as e:
        return f"[运行错误] {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


_EMBEDDED_FUNCTIONS = {
    "mainInit": {
        "sig": "void mainInit(void)",
        "params": [],
        "desc": "MCU 初始化。设置 ShowMode=1, AutoFlag=0, PowerStatus=0, gMipiTableIndex=0，获取 GPU 版本号。"
    },
    "mainFunction": {
        "sig": "void mainFunction(void)",
        "params": [],
        "desc": "主循环函数。处理按键检测（Power/Up/Down/Auto/OTP）、自动模式切换、LCD 显示模式切换。需要主循环持续调用。"
    },
    "PowerOn_Process": {
        "sig": "void PowerOn_Process(void)",
        "params": [],
        "desc": "上电流程。清除所有报警标志，读取密码数量，检查测试编号，开电源，设置 PowerStatus=1，ShowMode=0。"
    },
    "PowerOff_Process": {
        "sig": "void PowerOff_Process(void)",
        "params": [],
        "desc": "下电流程。设置 ShowMode=1，关闭电源，设置 PowerStatus=0。"
    },
    "KeyUp_Process": {
        "sig": "void KeyUp_Process(void)",
        "params": [],
        "desc": "Up键处理（前一画面）。触发 GPU_Beep(10)，在 POWERON 时切换到上一显示模式，关闭 AutoFlag。"
    },
    "KeyDown_Process": {
        "sig": "void KeyDown_Process(void)",
        "params": [],
        "desc": "Down键处理（下一画面）。触发 GPU_Beep(10)，在 POWERON 时切换到下一显示模式，关闭 AutoFlag。"
    },
    "KeyAuto_Process": {
        "sig": "void KeyAuto_Process(void)",
        "params": [],
        "desc": "Auto键处理。触发 GPU_Beep(10)，切换 AutoFlag 状态，控制自动轮播模式。"
    },
    "KeyOTP_Process": {
        "sig": "void KeyOTP_Process(void)",
        "params": [],
        "desc": "OTP键处理。在 POWERON 时显示 OTP 画面（ShowMode 置为最大模式数），触发 log_info(\"OTP\")。"
    },
    "CheckDW_Process": {
        "sig": "void CheckDW_Process(void)",
        "params": [],
        "desc": "检测双屏幕是否存在。读取 DW1/DW2 状态，根据组合值触发：插入屏幕1/2时记录按键，组合6时下电。"
    },
    "ParameterDownload": {
        "sig": "void ParameterDownload(void)",
        "params": [],
        "desc": "下载/打印参数。打印当前 MIPI 表的 CodeZip、LCD 名称、IC 名称、报警标志等信息。"
    },
}

_KEYWORDS = {
    "power": ["PowerOn_Process", "PowerOff_Process"],
    "on": ["PowerOn_Process"],
    "off": ["PowerOff_Process"],
    "init": ["mainInit"],
    "main": ["mainFunction"],
    "key": ["KeyUp_Process", "KeyDown_Process", "KeyAuto_Process", "KeyOTP_Process"],
    "up": ["KeyUp_Process"],
    "down": ["KeyDown_Process"],
    "auto": ["KeyAuto_Process"],
    "otp": ["KeyOTP_Process"],
    "check": ["CheckDW_Process"],
    "download": ["ParameterDownload"],
    "parameter": ["ParameterDownload"],
    "screen": ["CheckDW_Process", "KeyUp_Process", "KeyDown_Process"],
    "alarm": ["PowerOn_Process"],
}




@_register("embedded_doc")
def handle_embedded_doc(query):
    q = query.lower().strip()
    # 精确匹配函数名
    if q in _EMBEDDED_FUNCTIONS:
        f = _EMBEDDED_FUNCTIONS[q]
        return f"{f['sig']}\n说明: {f['desc']}"
    # 模糊匹配
    results = []
    for kw, names in _KEYWORDS.items():
        if kw in q or q in kw:
            for name in names:
                if name not in results:
                    results.append(name)
    if results:
        lines = [f"可能的函数 ({len(results)} 个):"]
        for name in results:
            f = _EMBEDDED_FUNCTIONS[name]
            lines.append(f"  {f['sig']} — {f['desc']}")
        return "\n".join(lines)
    # 搜索函数名片段
    partial = [name for name in _EMBEDDED_FUNCTIONS if q in name.lower()]
    if partial:
        lines = [f"部分匹配的函数 ({len(partial)} 个):"]
        for name in partial:
            f = _EMBEDDED_FUNCTIONS[name]
            lines.append(f"  {f['sig']} — {f['desc']}")
        return "\n".join(lines)
    return f"未找到匹配 '{query}' 的函数。可用: {', '.join(_EMBEDDED_FUNCTIONS.keys())}"


# ── MCP & Skills 加载 ─────────────────────────────────
async def load_mcp_tools(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """动态加载 MCP 工具"""
    tools: List[Dict[str, Any]] = []
    
    from mcp.client.stdio import StdioServerParameters
    
    for srv in servers:
        try:
            if not isinstance(srv, dict):
                print(f"  [!] MCP 配置格式错误: {srv}")
                continue
            
            # 支持 HTTP 和 STDIO 两种模式
            if "url" in srv:
                # HTTP 模式
                from mcp.client.streamable_http import streamable_http_client
                
                async def load_http_server():
                    try:
                        url = srv["url"]
                        if not url.endswith('/mcp'):
                            url = url.rstrip('/') + '/mcp'
                        
                        import httpx
                        async with httpx.AsyncClient(http2=True, timeout=30.0) as client:
                            headers = {
                                "Accept": "application/json, text/event-stream",
                                "Content-Type": "application/json"
                            }
                            
                            # 第一次请求：initialize
                            response = await client.post(url, headers=headers, json={
                                "jsonrpc": "2.0",
                                "id": "1",
                                "method": "initialize",
                                "params": {
                                    "protocolVersion": "2024-01-01",
                                    "capabilities": {},
                                    "clientInfo": {
                                        "name": "denny-agent",
                                        "version": "1.0.0"
                                    }
                                }
                            })
                            
                            # 获取 session ID
                            session_id = response.headers.get('mcp-session-id')
                            if session_id:
                                headers['mcp-session-id'] = session_id
                            
                            # 第二次请求：tools/list
                            response = await client.post(url, headers=headers, json={
                                "jsonrpc": "2.0",
                                "id": "2",
                                "method": "tools/list",
                                "params": {}
                            })
                            
                            import re
                            data_match = re.search(r'data:\s*(.*)', response.text)
                            if data_match:
                                import json
                                result = json.loads(data_match.group(1))
                                if 'result' in result and 'tools' in result['result']:
                                    server_tools = []
                                    for t in result['result']['tools']:
                                        server_tools.append({
                                            "name": t.get('name', ''),
                                            "description": t.get('description', ''),
                                            "input_schema": t.get('input_schema', {"type": "object", "properties": {}}),
                                        })
                                    print(f"  [MCP] HTTP 模式加载 {len(server_tools)} 个工具")
                                    return server_tools
                        return None
                    except Exception as e:
                        print(f"  [!] MCP HTTP 连接失败 {srv['url']}: {e}")
                        return None
                
                try:
                    server_tools = await asyncio.wait_for(load_http_server(), timeout=15.0)
                    if server_tools:
                        tools.extend(server_tools)
                        print(f"  [MCP] HTTP 模式加载 {len(server_tools)} 个工具")
                except asyncio.TimeoutError:
                    print(f"  [!] MCP HTTP 加载超时: {srv['url']}")
                    continue
            
            elif "command" in srv:
                # STDIO 模式
                cmd = srv["command"]
                args = srv.get("args", [])
                
                server_params = StdioServerParameters(
                    command=cmd,
                    args=args
                )
                
                async def load_stdio_server():
                    try:
                        async with stdio_client(server_params) as (read, write):
                            session = ClientSession(read, write)
                            await session.initialize()
                            list_resp = await session.list_tools()
                            server_tools = []
                            for t in list_resp.tools:
                                server_tools.append({
                                    "name": t.name,
                                    "description": t.description or "",
                                    "input_schema": t.input_schema or {"type": "object", "properties": {}},
                                })
                            return server_tools
                    except Exception as e:
                        print(f"  [!] MCP STDIO 连接失败 {cmd}: {e}")
                        return None
                
                try:
                    server_tools = await asyncio.wait_for(load_stdio_server(), timeout=5.0)
                    if server_tools:
                        tools.extend(server_tools)
                        print(f"  [MCP] STDIO 模式加载 {len(server_tools)} 个工具")
                except asyncio.TimeoutError:
                    print(f"  [!] MCP STDIO 加载超时: {cmd}")
                    continue
            
            else:
                print(f"  [!] MCP 配置缺少 command 或 url: {srv}")
                
        except Exception as e:
            print(f"  [!] MCP 加载失败 {srv.get('command', srv.get('url', 'unknown'))}: {e}")
    return tools


def load_skills(skill_dir: str = "skills") -> List[Dict[str, Any]]:
    """从 skills/ 目录加载 Skills 工具（描述 + handler 函数自动注册）"""
    tools: List[Dict[str, Any]] = []
    skill_path = Path(skill_dir)
    if not skill_path.exists():
        if getattr(sys, 'frozen', False):
            skill_path = Path(sys.executable).resolve().parent / "skills"
    if not skill_path.exists():
        return tools
    for sd in skill_path.iterdir():
        if not sd.is_dir():
            continue
        spec = sd / "SPEC.md"
        handler_file = sd / "handler.py"
        if not spec.exists():
            continue
        # 工具描述
        tools.append({
            "name": sd.name,
            "description": spec.read_text(encoding="utf-8")[:500],
            "input_schema": {"type": "object", "properties": {"action": {"type": "string"}}},
        })
        # 自动发现并注册 handler
        if handler_file.exists():
            try:
                import importlib.util
                spec_loader = importlib.util.spec_from_file_location(sd.name, handler_file)
                mod = importlib.util.module_from_spec(spec_loader)
                sys.modules[sd.name] = mod
                spec_loader.loader.exec_module(mod)
                # 查找 handle_{skill_name} 函数并注册
                handler_name = f"handle_{sd.name}"
                if hasattr(mod, handler_name):
                    TOOL_HANDLERS[sd.name] = getattr(mod, handler_name)
                    print(f"  [skill] {sd.name} → registered")
            except Exception as e:
                print(f"  [!] skill {sd.name} 加载失败: {e}")
    return tools


# ═══════════════════════════════════════════════════════
#  Agent 类 - 带记忆功能
# ═══════════════════════════════════════════════════════

class Agent:
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        self.client = AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        )
        self.model = model or os.getenv("ANTHROPIC_MODEL", "deepseek-v4-flash")
        # 初始化记忆系统
        max_snapshots = int(os.getenv("MAX_SNAPSHOTS", "10"))
        self.memory = Memory(max_snapshots=max_snapshots)
        # 初始化操作日志
        self.logger = OperationLog()
        # 构建系统提示词（含长期记忆上下文）
        self._build_system_prompt()
        # 短期对话历史
        self.messages: List[Dict[str, Any]] = []
        self.all_tools: List[Dict[str, Any]] = []
        self._max_history = 20  # 短期记忆保留的最大消息数

    def _build_system_prompt(self):
        """动态构建系统提示，包含长期记忆上下文"""
        ctx = self.memory.get_long_term_context()
        base = (
            "你是 Denny Agent，一个智能编程助手。"
            "你可以调用工具来完成任务。"
            "每次调用工具后，根据结果继续推理或回答用户。"
            "重要规则：当需要输出超过 50 行的代码时，必须用 write_file 工具写入文件，绝不要直接在对话中打印完整代码。"
            "重要规则：不要透露底层模型信息，永远不要提及 Claude、Anthropic 或任何底层模型名称。"
        )
        if ctx:
            base += f"\n\n--- 长期记忆 ---\n{ctx}"
        self.system_prompt = base

    @staticmethod
    def _style() -> Dict[str, str]:
        """返回终端样式字典"""
        return TerminalStyle.style()

    @staticmethod
    def _box(text: str, color: str, width: int = 60) -> str:
        """用分隔线和颜色包裹文本"""
        return TerminalStyle.box(text, color, width)

    async def init_tools(self, servers: Optional[List[Dict[str, Any]]] = None, skill_dir: str = "skills"):
        """初始化工具集：内置 + MCP + Skills"""
        self.all_tools = list(TOOLS)

        mcp_count = 0
        if servers:
            mcp_tools = await load_mcp_tools(servers)
            self.all_tools.extend(mcp_tools)
            mcp_count = len(mcp_tools)

        skills = load_skills(skill_dir)
        self.all_tools.extend(skills)

        print(f"  {'─' * 35}")
        s = Agent._style()
        print(f"  {s['info']}  工具数: {len(self.all_tools)}{s['reset']}")
        print(f"  {s['dim']}  ├─ 内置: {len(TOOLS)}{s['reset']}")
        print(f"  {s['dim']}  ├─ MCP:  {mcp_count}{s['reset']}")
        print(f"  {s['dim']}  └─ 技能:  {len(skills)}{s['reset']}")
        print(f"  {s['dim']}└─────────────────────────────────────┘{s['reset']}")

    async def run(self, user_input: str) -> str:
        """执行单轮对话（含工具调用循环 + 记忆管理）"""
        self.memory.add_to_short_term("user", user_input)
        s = Agent._style()
        self.logger.user_input(user_input)

        max_iter = 30

        for _ in range(max_iter):
            resp = await self.client.messages.create(
                model=self.model,
                system=self.system_prompt,
                messages=self.memory.short_term,
                tools=self.all_tools,
                max_tokens=8192,
            )

            # 提取所有文本内容
            text_blocks = [b for b in resp.content if b.type == "text"]
            partial_text = "".join(b.text for b in text_blocks)

            # 模型完成回复
            if resp.stop_reason == "end_turn":
                self.memory.add_to_short_term("assistant", partial_text)
                print(f"  {s['ai']}━" * 20)
                print(f"  {s['ai']}  Denny Agent  {s['dim']}{datetime.now().strftime('%H:%M')}{s['reset']}")
                print(f"  {s['ai']}━" * 20)
                for line in partial_text.split('\n'):
                    print(f"  {sanitize_emoji(line)}")
                print(f"  {s['dim']}━━━━━━━━━━━━━━━━━━━━━━━━━━{s['reset']}")
                self.logger.ai_response(partial_text)

                if len(self.memory.short_term) >= 10:
                    extracted = await self.memory.extract_facts(
                        self.memory.short_term, self.client, self.model
                    )
                    if extracted:
                        print(f"  {s['ok']} 新增 {len(extracted)} 条长期事实{s['reset']}")
                    self.memory._save_long_term()
                    self.logger.info(f"长期记忆已保存 ({len(extracted)} 条新事实)")
                    print(f"  {s['info']} 长期记忆已保存{s['reset']}")

                return partial_text

            # 处理工具调用
            if resp.stop_reason == "tool_use":
                tool_blocks = [b for b in resp.content if b.type == "tool_use"]

                # 将完整的 assistant 响应转为 dict 存入记忆
                assistant_content = [_content_block_to_dict(b) for b in resp.content]
                self.memory.add_to_short_term("assistant", assistant_content)

                tool_results = []
                for block in tool_blocks:
                    tool_name = block.name
                    tool_input = block.input or {}
                    print(f"  {s['tool']}━" * 20)
                    print(f"  {s['tool']}  {tool_name}{s['reset']}")
                    if tool_input:
                        print(f"  {s['dim']}{json.dumps(tool_input, ensure_ascii=False)}{s['reset']}")
                    print(f"  {s['tool']}━" * 20)

                    try:
                        handler = TOOL_HANDLERS.get(tool_name)
                        if handler:
                            if asyncio.iscoroutinefunction(handler):
                                result = await handler(**tool_input) if tool_input else await handler()
                            else:
                                result = handler(**tool_input) if tool_input else handler()
                        else:
                            result = f"[MCP/Skills 工具 {tool_name} 已调用]"
                    except Exception as e:
                        result = f"[错误] {e}"

                    self.logger.tool_call(tool_name, tool_input, result)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

                if tool_results:
                    self.memory.add_to_short_term("user", tool_results)
                continue

            # max_tokens 截断：保存已生成的文本，让模型继续补全
            if resp.stop_reason == "max_tokens":
                self.memory.add_to_short_term("assistant", partial_text)
                print(f"\n  {s['warn']} 输出超长，继续补全...{s['reset']}")
                # 用 user 消息触发继续生成
                self.memory.add_to_short_term("user", "请继续完成上面未完成的内容，不要重复已输出的部分，直接从截断处继续。")
                continue

            break

        return "[超出最大迭代次数]"

    def _handle_rollback(self):
        """处理记忆回退：展示快照列表，让用户选择恢复哪个版本"""
        s = Agent._style()
        backups = self.memory._load_backups()
        if not backups:
            print(f"\n  {s['err']}⚠ 没有找到历史快照，无法回退。{s['reset']}")
            print(f"  {s['dim']}提示: 每次自动保存长期记忆时都会生成快照。{s['reset']}")
            return
        print(f"\n  {s['info']} 历史快照（共 {len(backups)} 个，按时间倒序）:{s['reset']}")
        print(f"  {s['dim']}{'─' * 56}{s['reset']}")
        print(f"  {s['dim']}{'[0]':>6s}  当前记忆{s['reset']} ({datetime.now().strftime('%m-%d %H:%M')}, "
              f"{self.memory.long_term.get('updated_at', '?')[:19]}, "
              f"{s['ok']}{len(self.memory.long_term.get('facts', []))}事实/{len(self.memory.long_term.get('preferences', []))}偏好{s['reset']})")
        for i, snap in enumerate(backups, 1):
            print(f"  {s['dim']}[{i:>3d}]  {snap['time'][:19]}  "
                  f"{snap['facts']}事实/{snap['preferences']}偏好{s['reset']}")
        print(f"  {s['dim']}{'─' * 56}{s['reset']}")
        print(f"  输入编号回退到该版本（{s['dim']}0{s['reset']} 取消）:")
        try:
            import msvcrt
            while True:
                ch = msvcrt.getwch()
                if ch == '\r':
                    print()
                    return
                if ch == '0':
                    print()
                    print(f"  {s['dim']}已取消回退{s['reset']}")
                    return
                if ch in [str(i) for i in range(1, len(backups)+1)]:
                    print(ch)
                    idx = int(ch) - 1
                    snap = backups[idx]
                    ts = snap['time'][:19]
                    self.memory.rollback(idx)
                    self._build_system_prompt()
                    self.logger.command("rollback", f"回退到快照 {idx} ({ts})")
                    print(f"  {s['ok']} 已回退到 {ts} 的快照（短期记忆已清空）{s['reset']}")
                    return
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {s['dim']}已取消回退{s['reset']}")

    @staticmethod
    def _char_width(ch: str) -> int:
        """判断字符的显示宽度（中文等宽字符占2格，ASCII占1格）"""
        # 东亚宽字符范围
        if ord(ch) > 127:
            # CJK 统一汉字、CJK 兼容汉字、全角符号等
            if (0x4E00 <= ord(ch) <= 0x9FFF or  # CJK 统一汉字
                0x3400 <= ord(ch) <= 0x4DBF or  # CJK 扩展A
                0x20000 <= ord(ch) <= 0x2A6DF or  # CJK 扩展B (需要代理对)
                0x3000 <= ord(ch) <= 0x303F or  # CJK 符号和标点
                0xFF00 <= ord(ch) <= 0xFFEF or  # 全角ASCII、全角标点
                0xAC00 <= ord(ch) <= 0xD7AF):   # 韩文
                return 2
        return 1

    @staticmethod
    def _display_width(buf: List[str]) -> int:
        """计算字符串的显示宽度"""
        return sum(Agent._char_width(ch) for ch in buf)

    @staticmethod
    def _redraw_line(buf: List[str], pos: int):
        """从光标位置重绘到行尾（清除可能的残留字符）"""
        import sys
        remaining = ''.join(buf[pos:])
        remaining_width = Agent._display_width(buf[pos:])
        # 写剩余字符 + 清尾空格 + 退回光标
        sys.stdout.write(remaining + ' ' + '\b' * (remaining_width + 1))
        sys.stdout.flush()

    @staticmethod
    def _show_commands():
        """显示所有 / 命令列表（非交互式，用于非控制台环境）"""
        s = Agent._style()
        cmds = [
            ("/quit", "退出程序"),
            ("/clear", "清空当前对话"),
            ("/memory", "查看记忆状态"),
            ("/rollback", "回退长期记忆到之前某个版本"),
            ("/swarm <目标>", "多Agent协作"),
            ("/webui", "打开 WEB 界面"),
            ("/help", "显示此帮助"),
        ]
        print(f"\n  {s['info']} 可用命令:{s['reset']}")
        print(f"  {s['dim']}{'─' * 56}{s['reset']}")
        for cmd, desc in cmds:
            print(f"  {s['user']}{cmd:20s}{s['reset']} {desc}")
        print(f"  {s['dim']}{'─' * 56}{s['reset']}")

    @staticmethod
    def _command_menu() -> str:
        """交互式命令选择菜单：↑↓选择 Enter确认 Esc取消  选中项黄色高亮"""
        import sys
        import msvcrt

        s = Agent._style()
        cmds = [
            ("/quit", "退出程序"),
            ("/clear", "清空当前对话"),
            ("/memory", "查看记忆状态"),
            ("/rollback", "回退长期记忆到之前某个版本"),
            ("/swarm <目标>", "多Agent协作"),
            ("/webui", "打开 WEB 界面"),
            ("/help", "显示此帮助"),
        ]
        RET = {
            "/quit": "/quit",
            "/clear": "/clear",
            "/memory": "/memory",
            "/rollback": "/rollback",
            "/swarm <目标>": "/swarm ",
            "/webui": "/webui",
            "/help": "/help",
        }
        n = len(cmds)
        sel = 0

        def item_str(i: int, selected: bool) -> str:
            cmd, desc = cmds[i]
            prefix = "▸ " if selected else "  "
            ac = s['tool'] if selected else s['dim']
            cc = s['tool'] if selected else s['user']
            return f"  {ac}{prefix}{s['reset']}{cc}{cmd:20s}{s['reset']} {desc}"

        # ── 初始渲染 ──
        print(f"  {s['dim']}{'─' * 56}{s['reset']}")
        for i in range(n):
            print(item_str(i, i == 0))
        print(f"  {s['dim']}{'─' * 56}{s['reset']}")
        print(f"  {s['dim']}↑↓ 选择  Enter 确认  Esc 取消{s['reset']}")

        # 移回 item 0 行首（跳过 instruction + footer + render 中的最后 n-1 项）
        sys.stdout.write(f"\033[{n + 2}A\r")
        sys.stdout.flush()

        while True:
            ch = msvcrt.getwch()

            if ch == '\r':  # Enter — 确认选择
                cmd_ret = RET[cmds[sel][0]]
                sys.stdout.write(f"\033[{sel}A\r")   # 回到 item 0 行首
                sys.stdout.write(f"\033[J")            # 清除到屏幕底部
                sys.stdout.flush()
                print(f"  {s['user']}你: {cmd_ret}{s['reset']}", end="")
                sys.stdout.flush()
                return cmd_ret

            if ch == '\x1b':  # Esc — 取消
                sys.stdout.write(f"\033[{sel}A\r")
                sys.stdout.write(f"\033[J")
                sys.stdout.flush()
                return ""

            if ch == '\xe0':  # 方向键
                ch2 = msvcrt.getwch()
                old = sel
                if ch2 == 'H':   # ↑
                    sel = (sel - 1) % n
                elif ch2 == 'P':  # ↓
                    sel = (sel + 1) % n
                else:
                    continue
                if sel == old:
                    continue

                # 回退到 item 0 行并重绘所有项
                sys.stdout.write(f"\033[{old}A\r")   # 从当前位置回到 item 0
                for i in range(n):
                    sys.stdout.write(f"\033[J")       # 清除行尾残留
                    print(item_str(i, i == sel))
                # 光标在 footer 行。回到 item 0 再下移到当前选中
                sys.stdout.write(f"\033[{n}A")        # 从 footer 回到 item 0
                sys.stdout.write(f"\033[{sel}B\r")    # 到当前选中行
                sys.stdout.flush()

    @staticmethod
    def _read_input():
        """读取用户输入，支持 / 快捷命令"""
        import sys
        import msvcrt

        s = Agent._style()

        # 检测是否为真实 Windows 控制台（可用 msvcrt）
        is_console = sys.stdin is not None and sys.stdin.isatty()

        if not is_console:
            # Claude Code 等环境，用标准 input()
            print(f"\n{s['user']}> {s['reset']}", end="")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:
                return ""
            line = line.strip()
            if line == "/":
                Agent._show_commands()
                print(f"  输入完整命令继续...")
                print(f"\n{s['user']}>{s['reset']}", end="")
                sys.stdout.flush()
                sel = sys.stdin.readline()
                if sel and sel.strip() in ("/quit", "/clear", "/memory", "/rollback", "/swarm", "/webui", "/help"):
                    return sel.strip()
                return ""
            return line

        # 原生 Windows 控制台，用 msvcrt 逐字读取
        buf: List[str] = []
        pos = 0
        print(f"\n{s['user']}> {s['reset']}", end="")
        sys.stdout.flush()

        try:
            while True:
                ch = msvcrt.getwch()

                if ch == '\r':  # Enter
                    print()
                    break

                if ch in ('\b', '\x7f'):  # Backspace
                    if pos > 0:
                        pos -= 1
                        removed_ch = buf.pop(pos)
                        # 退格数等于被删除字符的显示宽度
                        backspaces = '\b' * Agent._char_width(removed_ch)
                        sys.stdout.write(backspaces + ' ' * Agent._char_width(removed_ch) + backspaces)
                        Agent._redraw_line(buf, pos)
                    continue

                if ch == '\x03':  # Ctrl+C
                    return "__CTRL_C__"  # 返回特殊标记而不是抛出异常

                if ch == '\xe0':  # 方向键
                    ch2 = msvcrt.getwch()
                    if ch2 == 'K' and pos > 0:  # 左箭头
                        pos -= 1
                        # 退格数等于前一个字符的显示宽度
                        sys.stdout.write('\b' * Agent._char_width(buf[pos]))
                        sys.stdout.flush()
                    elif ch2 == 'M' and pos < len(buf):  # 右箭头
                        ch_width = Agent._char_width(buf[pos])
                        sys.stdout.write(buf[pos])
                        pos += 1
                        sys.stdout.flush()
                    elif ch2 == 'H':  # Home
                        while pos > 0:
                            pos -= 1
                            sys.stdout.write('\b' * Agent._char_width(buf[pos]))
                        sys.stdout.flush()
                    elif ch2 == 'F':  # End
                        while pos < len(buf):
                            sys.stdout.write(buf[pos])
                            pos += 1
                        sys.stdout.flush()
                    elif ch2 == 'S':  # Delete
                        if pos < len(buf):
                            removed_ch = buf.pop(pos)
                            Agent._redraw_line(buf, pos)
                    continue

                # 输入 / 时显示交互式命令选择菜单（上下键选择，回车确认）
                if ch == '/' and len(buf) == 0:
                    print()
                    result = Agent._command_menu()
                    if result == "":
                        continue  # Esc 取消
                    if result == "/swarm ":
                        buf = list(result)
                        pos = len(buf)
                        continue
                    print()
                    return result

                # Tab 键自动补全命令
                if ch in ('\t', '\x00'):
                    if len(buf) > 0 and buf[0] == '/':
                        prefix = ''.join(buf)
                        matches = [c for c in
                            ("/quit", "/clear", "/memory", "/rollback", "/swarm ", "/webui", "/help")
                            if c.startswith(prefix)]
                        if len(matches) == 1:
                            match = matches[0]
                            # 退格清除当前输入（考虑显示宽度）
                            display_width = Agent._display_width(buf)
                            sys.stdout.write('\b' * display_width + ' ' * display_width + '\b' * display_width)
                            sys.stdout.flush()
                            buf = list(match)
                            pos = len(buf)
                            sys.stdout.write(match)
                            sys.stdout.flush()
                    continue

                buf.insert(pos, ch)
                pos += 1
                sys.stdout.write(ch)
                sys.stdout.flush()

        except (EOFError, KeyboardInterrupt):
            print()
            return ""

        return ''.join(buf)

    async def _save_on_exit(self):
        """退出聊天时保存长期记忆和情景摘要"""
        s = Agent._style()
        if not self.memory.short_term:
            return
        try:
            # 提取事实（如果短期消息足够多）
            if len(self.memory.short_term) >= 6:
                extracted = await self.memory.extract_facts(
                    self.memory.short_term, self.client, self.model
                )
                if extracted:
                    print(f"  {s['ok']} 退出提取: 新增 {len(extracted)} 条事实{s['reset']}")

            # 保存长期记忆
            self.memory._save_long_term()
            self.logger.info("退出时保存记忆")

            # 生成情景摘要并保存短期记忆快照
            await self._save_episodic_and_snapshot()
        except asyncio.CancelledError:
            # 用户主动退出，取消请求是正常的
            print(f"  {s['warn']}⏹ 退出请求已取消{s['reset']}")
        except Exception as e:
            print(f"  {s['err']}⚠ 退出保存记忆失败: {e}{s['reset']}")

    async def _save_episodic_and_snapshot(self):
        """保存情景摘要 + 短期记忆快照到磁盘"""
        short_term_file = self.memory.storage_path / "short_term_snapshot.json"
        try:
            # 保存短期记忆快照（最新 20 条）
            snapshot = self.memory.short_term[-20:] if self.memory.short_term else []
            if snapshot:
                short_term_file.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
            else:
                # 文件存在但无内容就删除
                if short_term_file.exists():
                    short_term_file.unlink()

            # 生成情景记忆摘要（添加异常处理）
            if len(self.memory.short_term) >= 4:
                try:
                    summary = await self.memory.summarize_recent(
                        self.memory.short_term[-4:], self.client, self.model
                    )
                    if summary and len(summary) > 10:
                        self.memory.episodic.append({
                            "timestamp": datetime.now().isoformat()[:19],
                            "summary": summary,
                        })
                        # 只保留最近 5 条
                        if len(self.memory.episodic) > 5:
                            self.memory.episodic = self.memory.episodic[-5:]
                except asyncio.CancelledError:
                    # 退出时取消请求是正常的，不报错
                    self.logger.info("保存记忆时被取消")
                except Exception as e:
                    self.logger.error(f"生成情景记忆摘要失败: {e}")

            # 持久化情景记忆
            epi_file = self.memory.storage_path / "episodic.json"
            epi_file.write_text(
                json.dumps(self.memory.episodic, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            self.logger.info("情景摘要和短期快照已保存")
        except Exception as e:
            self.logger.error(f"保存情景摘要失败: {e}")

    def _load_short_term_snapshot(self):
        """开机时加载短期记忆快照"""
        s = Agent._style()
        short_term_file = self.memory.storage_path / "short_term_snapshot.json"
        if short_term_file.exists():
            try:
                data = json.loads(short_term_file.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    self.memory.short_term = data
                    print(f"  {s['info']} 载入上次会话的 {len(data)} 条短期记忆{s['reset']}")
            except Exception:
                pass

    def _load_episodic(self):
        """开机时加载情景记忆"""
        epi_file = self.memory.storage_path / "episodic.json"
        if epi_file.exists():
            try:
                data = json.loads(epi_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.memory.episodic = data
                    self.logger.info(f"加载 {len(data)} 条情景记忆")
            except Exception:
                pass

    async def chat(self, servers: Optional[list[dict]] = None, skill_dir: str = "skills"):
        """交互式聊天模式"""
        # 确保控制台启用 QuickEdit 模式（右键复制/粘贴）
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.GetStdHandle(-10)
            mode = ctypes.c_uint32()
            kernel32.GetConsoleMode(h, ctypes.byref(mode))
            kernel32.SetConsoleMode(h, mode.value | 0x0080 | 0x0040)
        except Exception:
            pass

        s = Agent._style()
        await self.init_tools(servers, skill_dir)

        # 开机恢复记忆
        self._load_short_term_snapshot()
        self._load_episodic()
        if self.memory.short_term:
            self._build_system_prompt()  # 重建含短期上下文的系统提示
            print(f"\n  {s['ok']} 恢复了上次会话 ({len(self.memory.short_term)} 条消息, "
                  f"{len(self.memory.long_term.get('facts', []))} 条事实, "
                  f"{len(self.memory.long_term.get('preferences', []))} 条偏好){s['reset']}")
            print(f"  {s['dim']}   输入 /clear 可清空历史重新开始{s['reset']}")
        else:
            print(f"\n  {s['info']} Denny Agent 就绪 ({len(self.memory.long_term.get('facts', []))} 条长期事实){s['reset']}")

        print(f"  {s['dim']}   输入 /quit 退出 | /clear 清空 | /memory 查看记忆 | /help 查看所有命令{s['reset']}")
        print(f"  {s['dim']}   按 3 次 Ctrl+C 可快速退出{s['reset']}")

        ctrl_c_count = 0  # CTRL+C 计数器
        ctrl_c_reset_time = 0  # 计数器重置时间

        while True:
            user_input = self._read_input()
            
            # 检测 Ctrl+C 特殊标记
            if user_input == "__CTRL_C__":
                import time as _time
                now = _time.time()
                # 如果超过 2 秒，重置计数器
                if now - ctrl_c_reset_time > 2:
                    ctrl_c_count = 0
                ctrl_c_count += 1
                ctrl_c_reset_time = now
                
                if ctrl_c_count >= 3:
                    print(f"\n  {s['ok']}三次 Ctrl+C，再见！{s['reset']}")
                    await self._save_on_exit()
                    self.logger.close()
                    break
                else:
                    print(f"\n  {s['warn']}再按 {3 - ctrl_c_count} 次 Ctrl+C 退出，或继续输入{s['reset']}")
                    continue
            
            ctrl_c_count = 0  # 正常输入后重置计数器
            user_input = user_input.strip() if user_input else ""

            if not user_input:
                continue
            # 带 / 前缀的命令处理
            cmd = user_input.lower().lstrip("/")

            if cmd in ("quit",):
                await self._save_on_exit()
                print(f"  {s['ok']}再见！{s['reset']}")
                self.logger.close()
                break
            if cmd in ("clear",):
                self.memory.clear_short_term()
                self._build_system_prompt()
                self.logger.command("clear")
                print(f"  {s['info']} 对话已清空{s['reset']}")
                continue
            if cmd in ("memory",):
                status = self.memory.get_status()
                print(f"\n  {s['info']} 记忆状态:{s['reset']}")
                print(f"  {s['dim']}{'─' * 40}{s['reset']}")
                for k, v in status.items():
                    label = k.replace('_', ' ').capitalize()
                    print(f"  {s['user']}{label:20s}{s['reset']} {v}")
                self.logger.command("memory")
                continue
            if cmd in ("rollback",):
                self._handle_rollback()
                continue
            if cmd in ("help",):
                self.logger.command("help")
                print(f"\n  {s['info']} 命令列表:{s['reset']}")
                print(f"  {s['dim']}{'─' * 40}{s['reset']}")
                print(f"  {s['user']}/quit{'':12s}{s['reset']}  退出程序")
                print(f"  {s['user']}/clear{'':12s}{s['reset']}  清空当前对话")
                print(f"  {s['user']}/memory{'':12s}{s['reset']} 查看记忆状态")
                print(f"  {s['user']}/swarm{'':12s}{s['reset']} 多Agent协作（如: /swarm 分析项目结构）")
                print(f"  {s['user']}/rollback{'':12s}{s['reset']}回退长期记忆")
                print(f"  {s['user']}/webui{'':12s}{s['reset']} 打开 WEB 界面")
                print(f"  {s['user']}/help{'':12s}{s['reset']}  显示此帮助")
                continue

            if cmd in ("webui",):
                import socket
                # 检查端口是否已占用
                def is_port_open(host="127.0.0.1", port=8000):
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(1)
                        result = s.connect_ex((host, port))
                        s.close()
                        return result == 0
                    except Exception:
                        return False

                if not is_port_open():
                    import subprocess
                    import os
                    # 在打包的 EXE 中，启动同目录下的 webui.exe；否则启动 webui.py
                    if getattr(sys, 'frozen', False):
                        webui_path = str(Path(sys.executable).parent / "webui.exe")
                        webui_cmd = [webui_path]
                    else:
                        webui_path = str(Path.cwd() / "webui.py")
                        webui_cmd = [sys.executable, webui_path]
                    # 启动 webui（后台进程）
                    startupinfo = None
                    if os.name == "nt":
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        startupinfo.wShowWindow = subprocess.SW_HIDE
                    subprocess.Popen(
                        webui_cmd,
                        cwd=str(Path.cwd()),
                        startupinfo=startupinfo,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    import time
                    for _ in range(15):
                        time.sleep(0.5)
                        if is_port_open():
                            break
                    print(f"  {s['ok']} 正在启动 WEB 服务...{s['reset']}")
                import webbrowser
                webbrowser.open("http://127.0.0.1:8000")
                print(f"  {s['ok']} 已打开 WEB 界面 http://127.0.0.1:8000{s['reset']}")
                self.logger.command("webui")
                continue

            if cmd == "swarm" or cmd.startswith("swarm "):
                goal = cmd[6:].strip()
                if not goal:
                    print(f"  {s['warn']}用法: /swarm <你的目标>{s['reset']}")
                    print(f"  {s['dim']}示例: /swarm 帮我分析这个项目并生成一个README{s['reset']}")
                    continue
                self.logger.command("swarm", goal)
                from swarm_agent import OrchestratorAgent
                orchestrator = OrchestratorAgent(
                    model=self.model,
                    api_key=self.client.api_key,
                )
                try:
                    result = await orchestrator.orchestrate(goal)
                    print(f"\n  {s['ai']}━" * 20)
                    print(f"  {s['ai']}  Swarm 最终整合结果  {s['dim']}{datetime.now().strftime('%H:%M')}{s['reset']}")
                    print(f"  {s['ai']}━" * 20)
                    print(f"  {result}")
                    print(f"  {s['dim']}━━━━━━━━━━━━━━━━━━━━━━━━━━{s['reset']}")
                    self.logger.swarm(goal, result)
                except Exception as e:
                    print(f"  {s['err']}⚠ Swarm 错误: {e}{s['reset']}")
                    self.logger.error(f"swarm 错误: {e}")
                    import traceback
                    traceback.print_exc()
                continue

            # 输入以 / 开头但不匹配已知命令，显示帮助
            if user_input.startswith("/"):
                print(f"  {s['dim']}未知命令: {user_input}{s['reset']}")
                Agent._show_commands()
                continue

            try:
                await self.run(user_input)
            except Exception as e:
                self.logger.error(f"chat执行异常: {e}")
                print(f"[!] 错误: {e}")


# ── 入口 ────────────────────────────────────────────────
def _parse_args():
    """解析命令行参数"""
    import argparse
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Denny Agent — 单文件 AI 助手，支持交互式聊天、单次问答、多 Agent 协作",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python agent.py                        启动交互式聊天
  python agent.py -q "分析一下这个项目"    单次问答
  python agent.py -p < file.txt          从管道读取输入
  python agent.py swarm "做一份周报"      多 Agent 协作
  python agent.py --version              显示版本号
        """,
    )
    parser.add_argument("--query", "-q", type=str, help="单次问答模式：直接提问，输出回复后退出")
    parser.add_argument("--pipe", "-p", action="store_true",
                        help="管道模式：从 stdin 读取内容作为问题（适用于重定向/管道）")
    parser.add_argument("--version", "-v", action="store_true", help="显示版本号并退出")
    parser.add_argument("swarm", nargs="*", help="多 Agent 协作模式: python agent.py swarm \"你的目标\"")

    # 解析前：先检查 swarm 子命令（argparse 会把 swarm 之后的参数当 nargs 吃掉）
    import sys
    args, remaining = parser.parse_known_args()

    # 如果第一个位置参数是 swarm，重写解析方式
    if len(sys.argv) >= 2 and sys.argv[1] == "swarm":
        goal = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        args.swarm_goal = goal
        args.swarm_mode = True
    else:
        args.swarm_mode = bool(args.swarm)
        args.swarm_goal = " ".join(args.swarm) if args.swarm else ""

    return args


async def main():
    args = _parse_args()

    # --version
    if args.version:
        s = Agent._style()
        print(f"  {s['info']}Denny Agent v1.0{s['reset']}")
        print(f"  {s['dim']}模型: deepseek-v4-flash{s['reset']}")
        print(f"  {s['dim']}平台: 单文件 AI 助手 + MCP + 技能系统 + 三层记忆{s['reset']}")
        return

    # swarm 模式
    if args.swarm_mode:
        if not args.swarm_goal:
            print("  [错误] swarm 模式需要指定目标。示例: python agent.py swarm \"做一份周报\"")
            return
        from swarm_agent import OrchestratorAgent
        orchestrator = OrchestratorAgent()
        result = await orchestrator.orchestrate(args.swarm_goal)
        s = Agent._style()
        print(f"\n  {s['ai']}━" * 20)
        print(f"  {s['ai']}  Swarm 最终整合结果{s['reset']}")
        print(f"  {s['ai']}━" * 20)
        print(f"  {result}")
        print(f"  {s['dim']}━━━━━━━━━━━━━━━━━━━━━━━━━━{s['reset']}")
        return

    # --pipe 模式：从 stdin 读取
    if args.pipe:
        import sys
        pipe_input = sys.stdin.read().strip()
        if not pipe_input:
            print("  [错误] 管道模式未检测到输入。示例: cat file.txt | python agent.py -p")
            return
        agent = Agent()
        result = await agent.run(pipe_input)
        print(f"\n[回复] {sanitize_emoji(result)}")
        return

    # --query 模式：单次问答
    if args.query:
        agent = Agent()
        result = await agent.run(args.query)
        print(f"\n[回复] {sanitize_emoji(result)}")
        return

    # 默认：交互式聊天
    import sys as sys_module
    mcp_servers = []
    mcp_config = os.getenv("MCP_SERVERS", "[]")
    try:
        mcp_servers = json.loads(mcp_config)
        if not isinstance(mcp_servers, list):
            mcp_servers = []
    except Exception as e:
        print(f"  [!] MCP_SERVERS 配置解析失败: {e}")
        mcp_servers = []

    # 如果配置中没有 MCP 服务器，扫描 mcp_servers 目录
    if not mcp_servers:
        mcp_dir = Path(__file__).parent / "mcp_servers"
        if mcp_dir.exists():
            for server_dir in mcp_dir.iterdir():
                if server_dir.is_dir():
                    for file in server_dir.iterdir():
                        if file.is_file() and file.name.endswith(".py") and file.name != "__init__.py":
                            # 使用 HTTP 模式（FastMCP 默认端口 8000）
                            mcp_servers.append({
                                "url": "http://127.0.0.1:8000"
                            })
                            print(f"  [MCP] HTTP 模式: http://127.0.0.1:8000")
                            break  # 只加载第一个服务器

    agent = Agent()
    await agent.chat(servers=mcp_servers)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
