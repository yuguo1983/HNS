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
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta

from anthropic import AsyncAnthropic
from mcp import ClientSession
from mcp.client.stdio import stdio_client


# ── 加载 .config 配置文件 ──────────────────────────────
def _load_config(path: str = ".config"):
    """读取 .config 文件，设置环境变量（优先 EXE 同目录，其次当前目录）"""
    config_path = None
    # 1. 优先找 EXE 所在目录下的 .config
    try:
        exe_dir = Path(__file__).resolve().parent
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / path
        if candidate.exists():
            config_path = candidate
    except Exception:
        pass
    # 2. 其次找当前工作目录下的 .config
    if config_path is None:
        cwd_candidate = Path.cwd() / path
        if cwd_candidate.exists():
            config_path = cwd_candidate

    if config_path and config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for k, v in data.items():
            os.environ[k] = str(v)
        print(f"[+] 配置已加载: {list(data.keys())} (来源: {config_path})")
    else:
        print(f"[!] 未找到 .config 配置文件，将使用环境变量或默认值")


_load_config()


# ═══════════════════════════════════════════════════════
#  记忆系统 - Memory System
# ═══════════════════════════════════════════════════════

class Memory:
    """
    三层记忆系统：
    1. 短期记忆 (Short-term): 当前会话的对话历史，受上下文窗口限制
    2. 长期记忆 (Long-term): 跨会话持久化的关键事实、偏好、知识
    3. 情景记忆 (Episodic): 最近对话的摘要，用于上下文回溯
    """

    def __init__(self, storage_path: str = ".agent_memory"):
        self.storage_path = Path(storage_path)
        self.short_term: list = []          # 当前对话轮次
        self.episodic: list = []            # 已结束的对话摘要
        self.long_term = {                  # 持久化事实
            "facts": [],                     # 提取的事实
            "preferences": [],               # 用户偏好
            "knowledge": {},                 # 领域知识
            "created_at": None,
            "updated_at": None,
        }
        self._load_long_term()

    def _load_long_term(self):
        """加载长期记忆"""
        mem_file = self.storage_path / "long_term.json"
        if mem_file.exists():
            try:
                data = json.loads(mem_file.read_text(encoding="utf-8"))
                self.long_term.update(data)
                print(f"  📖 加载长期记忆: {len(self.long_term.get('facts', []))} 条事实, "
                      f"{len(self.long_term.get('preferences', []))} 条偏好")
            except Exception as e:
                print(f"  [!] 长期记忆加载失败: {e}")

    def save_long_term(self):
        """持久化长期记忆"""
        self.long_term["updated_at"] = datetime.now().isoformat()
        if self.long_term.get("created_at") is None:
            self.long_term["created_at"] = self.long_term["updated_at"]
        mem_file = self.storage_path / "long_term.json"
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        mem_file.write_text(json.dumps(self.long_term, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_to_short_term(self, role: str, content):
        """添加短期记忆（对话历史）"""
        self.short_term.append({"role": role, "content": content})

    def summarize_recent(self, messages: list, llm_client, model: str) -> str:
        """
        使用 LLM 对最近对话进行摘要，释放上下文空间
        返回摘要文本，用于压缩历史
        """
        try:
            resp = llm_client.messages.create(
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
            summary = resp.content[0].text if resp.content else "(摘要生成失败)"
            self.episodic.append({
                "timestamp": datetime.now().isoformat(),
                "summary": summary,
                "message_count": len(messages),
            })
            # 只保留最近5条摘要
            self.episodic = self.episodic[-5:]
            return summary
        except Exception as e:
            print(f"  [!] 摘要生成失败: {e}")
            return ""

    def extract_facts(self, messages: list, llm_client, model: str) -> list:
        """
        从对话中提取重要事实和偏好，存入长期记忆
        """
        try:
            resp = llm_client.messages.create(
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
            text = resp.content[0].text if resp.content else "[]"
            # 尝试解析 JSON
            import re
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if json_match:
                items = json.loads(json_match.group())
                for item in items:
                    if isinstance(item, dict) and "content" in item:
                        key = item.get("type", "fact")
                        content = item["content"]
                        if key == "preference" and content not in self.long_term["preferences"]:
                            self.long_term["preferences"].append(content)
                        elif key == "fact" and content not in self.long_term["facts"]:
                            self.long_term["facts"].append(content)
                        else:
                            # 视为一般知识
                            self.long_term["knowledge"].update({content: True})
                return items
        except Exception as e:
            print(f"  [!] 事实提取失败: {e}")
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

    def get_status(self) -> dict:
        """获取记忆状态"""
        return {
            "short_term_messages": len(self.short_term),
            "episodic_summaries": len(self.episodic),
            "long_term_facts": len(self.long_term.get("facts", [])),
            "long_term_preferences": len(self.long_term.get("preferences", [])),
        }


# ── 内置工具定义 ────────────────────────────────────────
TOOLS = [
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
def _content_block_to_dict(block):
    """将 SDK 的 content block 对象转为普通 dict，确保序列化兼容"""
    if isinstance(block, dict):
        return block
    for method in ("model_dump", "dict", "to_dict"):
        fn = getattr(block, method, None)
        if callable(fn):
            return fn()
    # 兜底：手动提取已知字段
    d = {"type": getattr(block, "type", "text")}
    for attr in ("text", "name", "input", "id", "tool_use_id", "content", "source"):
        if hasattr(block, attr):
            d[attr] = getattr(block, attr)
    return d


# ── 工具执行函数 ────────────────────────────────────────
TOOL_HANDLERS = {}

def _register(name):
    def decorator(fn):
        TOOL_HANDLERS[name] = fn
        return fn
    return decorator


@_register("web_search")
def handle_web_search(q):
    try:
        import urllib.request, urllib.parse
        q = urllib.parse.quote(q)
        r = urllib.request.urlopen(f"https://html.duckduckgo.com/html/?q={q}", timeout=15)
        return r.read().decode("utf-8")[:2000]
    except Exception as e:
        return f"搜索失败: {e}"


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
async def load_mcp_tools(servers: list[dict]) -> list[dict]:
    """动态加载 MCP 工具"""
    tools = []
    for srv in servers:
        try:
            cmd = srv["command"]
            args = srv.get("args", [])
            async with stdio_client(cmd, args) as (read, write):
                session = ClientSession(read, write)
                await session.initialize()
                list_resp = await session.list_tools()
                for t in list_resp.tools:
                    tools.append({
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": t.input_schema or {"type": "object", "properties": {}},
                    })
        except Exception as e:
            print(f"[!] MCP 加载失败 {cmd}: {e}")
    return tools


def load_skills(skill_dir: str = "skills") -> list[dict]:
    """从 skills/ 目录加载 Skills 工具（描述 + handler 函数自动注册）"""
    tools = []
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
        self.memory = Memory()
        # 构建系统提示词（含长期记忆上下文）
        self._build_system_prompt()
        # 短期对话历史
        self.messages: list = []
        self.all_tools = []
        self._max_history = 20  # 短期记忆保留的最大消息数

    def _build_system_prompt(self):
        """动态构建系统提示，包含长期记忆上下文"""
        ctx = self.memory.get_long_term_context()
        base = (
            "你是 Denny Agent，一个智能编程助手。"
            "你可以调用工具来完成任务。"
            "每次调用工具后，根据结果继续推理或回答用户。"
            "重要规则：当需要输出超过 50 行的代码时，必须用 write_file 工具写入文件，绝不要直接在对话中打印完整代码。"
        )
        if ctx:
            base += f"\n\n--- 长期记忆 ---\n{ctx}"
        self.system_prompt = base

    async def init_tools(self, servers: Optional[list[dict]] = None, skill_dir: str = "skills"):
        """初始化工具集：内置 + MCP + Skills"""
        self.all_tools = list(TOOLS)

        mcp_count = 0
        if servers:
            mcp_tools = await load_mcp_tools(servers)
            self.all_tools.extend(mcp_tools)
            mcp_count = len(mcp_tools)

        skills = load_skills(skill_dir)
        self.all_tools.extend(skills)

        print(f"[+] 总工具: {len(self.all_tools)} (内置 {len(TOOLS)} + MCP {mcp_count} + Skills {len(skills)})")

    async def run(self, user_input: str) -> str:
        """执行单轮对话（含工具调用循环 + 记忆管理）"""
        self.memory.add_to_short_term("user", user_input)
        print(f"\n📝 短期记忆: 已有 {len(self.memory.short_term)} 条消息")

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
                print(f"\n{'='*50}\n🤖 Agent: {partial_text}\n{'='*50}")

                if len(self.memory.short_term) >= 10:
                    extracted = self.memory.extract_facts(
                        self.memory.short_term, self.client, self.model
                    )
                    if extracted:
                        print(f"  💡 记忆提取: 新增 {len(extracted)} 条")
                    self.memory.save_long_term()
                    print(f"  💾 长期记忆已保存")

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
                    print(f"\n🔧 {tool_name}({json.dumps(tool_input, ensure_ascii=False)})")

                    try:
                        handler = TOOL_HANDLERS.get(tool_name)
                        if handler:
                            result = handler(**tool_input) if tool_input else handler()
                        else:
                            result = f"[MCP/Skills 工具 {tool_name} 已调用]"
                    except Exception as e:
                        result = f"[错误] {e}"

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
                print(f"\n📝 输出超长已截断，继续补全...")
                # 用 user 消息触发继续生成
                self.memory.add_to_short_term("user", "请继续完成上面未完成的内容，不要重复已输出的部分，直接从截断处继续。")
                continue

            break

        return "[超出最大迭代次数]"

    async def chat(self, servers: Optional[list[dict]] = None, skill_dir: str = "skills"):
        """交互式聊天模式"""
        await self.init_tools(servers, skill_dir)
        print("\n🚀 Denny Agent 就绪")
        print("   输入 'quit' 退出 | 'clear' 清空对话 | 'memory' 查看记忆状态")

        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input.lower() == "quit":
                print("再见！")
                break
            if user_input.lower() == "clear":
                self.memory.clear_short_term()
                self._build_system_prompt()
                print("对话已清空")
                continue
            if user_input.lower() == "memory":
                status = self.memory.get_status()
                print(f"\n📊 记忆状态:")
                for k, v in status.items():
                    print(f"   • {k}: {v}")
                continue

            try:
                await self.run(user_input)
            except Exception as e:
                print(f"[!] 错误: {e}")


# ── 入口 ────────────────────────────────────────────────
async def main():
    # MCP 服务器配置（按需取消注释并添加）
    mcp_servers = [
        # {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
    ]

    agent = Agent()
    await agent.chat(servers=mcp_servers)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
