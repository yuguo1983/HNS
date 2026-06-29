# Denny Agent

> 单文件 Python AI 智能体 — 内置工具 / MCP 扩展 / Skills 技能系统 / 三层记忆 / Swarm 多 Agent 协作 / Web UI / 流式输出

使用 Anthropic SDK 指向 DeepSeek（或其他兼容）代理，开箱即用。一行命令安装，任意目录运行。

---

## ✨ 特性

| 能力 | 说明 |
|------|------|
| **流式输出** | 实时显示思考过程（💭）+ 文本生成 + 工具参数 JSON 逐步生成 |
| **内置工具** | 14 个：web 搜索、文件读写、命令执行、目录列表、计算器、图像读取/处理、代码执行（Python/C/JS/TS/Java/Go）、代码搜索、文件查找、打开文件、嵌入式固件 API 查询 |
| **MCP 扩展** | 支持 STDIO 和 HTTP 两种模式的 MCP 服务器，动态加载外部工具 |
| **Skills 技能** | 从 `skills/` 目录自动发现，每个技能含 `SPEC.md` 描述 + `handler.py` 处理函数 |
| **三层记忆** | 短期（会话内）/ 长期（跨会话事实与偏好，持久化到 JSON）/ 情景（对话摘要，保留最近 5 条） |
| **Swarm 多 Agent** | `/swarm <目标>` 触发多智能体协作，自动分解任务、分派子 Agent、整合结果 |
| **Web UI** | `/webui` 启动 FastAPI + SSE 流式 Web 界面，浏览器实时对话 |
| **交互式命令菜单** | 上下键选择、回车确认、黄色高亮 |
| **一键安装** | PowerShell 一行命令安装，自动配置 PATH |

---

## 🚀 一键安装

### Windows（PowerShell）

```powershell
irm https://raw.githubusercontent.com/yuguo1983/HNS/main/scripts/install.ps1 | iex
```

安装脚本会：
1. 检查 Python 3.10+ 和 git
2. 从 atomgit 克隆代码到 `%USERPROFILE%\denny-agent`（国内快，主源）
3. `pip install -e .` 安装依赖并注册 `denny` 命令到 PATH
4. 生成 `.config` 配置模板（需填入 API 密钥）

> **atomgit 主源**：安装脚本本体从 GitHub raw 取（9KB，秒下），项目代码从 [atomgit.com/denny168/agent](https://atomgit.com/denny168/agent.git) 克隆（国内访问快）。
>
> **纯 atomgit 方案**（不用 GitHub）：
> ```powershell
> git clone --depth 1 https://atomgit.com/denny168/agent.git $env:USERPROFILE\denny-agent
> cd $env:USERPROFILE\denny-agent
> powershell -ExecutionPolicy Bypass -File scripts/install.ps1
> ```

### 配置 API 密钥

编辑 `%USERPROFILE%\denny-agent\.config`：

```json
{
  "ANTHROPIC_API_KEY": "sk-你的密钥",
  "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
  "ANTHROPIC_MODEL": "deepseek-v4-flash",
  "MAX_SNAPSHOTS": 10,
  "MCP_SERVERS": []
}
```

> 不需要 MCP 时保持 `"MCP_SERVERS": []` 即可。

### 启动

```powershell
denny                    # 交互式聊天
denny -q "你好"          # 单次问答
denny swarm "分析项目"   # 多 Agent 协作
```

### 升级 / 卸载

```powershell
# 升级（重跑安装命令即可）
irm https://raw.githubusercontent.com/yuguo1983/HNS/main/scripts/install.ps1 | iex

# 卸载
pip uninstall denny-agent
Remove-Item -Recurse $env:USERPROFILE\denny-agent
```

---

## 📦 手动安装（开发者）

```bash
git clone https://atomgit.com/denny168/agent.git
cd agent
pip install -e .
```

---

## 🗂 项目结构

```
denny-agent/
├── agent.py            # 智能体核心（~2200 行，含记忆系统 + 工具调用 + 流式输出）
├── swarm_agent.py      # Swarm 多 Agent 协作模块
├── webui.py            # FastAPI Web UI 服务（SSE 流式）
├── webui/static/       # Web 前端（index.html + app.js）
├── utils.py            # 配置加载 / 终端样式 / 序列化工具
├── serial_comm.py      # 串口通信模块
├── serial_gui.py       # 串口 GUI
├── skills/             # 技能插件目录（自动发现）
│   ├── analyze_compile/    # 编译日志分析
│   ├── camera/             # 摄像头控制
│   ├── fix_undefined_vars/ # 链接错误自动修复
│   ├── image_analysis/     # 图像理解/OCR/人脸检测
│   └── ymodem/             # YMODEM 串口文件发送
├── scripts/
│   └── install.ps1     # 一键安装脚本
├── pyproject.toml      # pip 打包配置（注册 denny 命令）
├── .config             # API 配置（gitignored）
├── .config.example     # 配置模板
└── .agent_memory/      # 记忆持久化目录（gitignored）
```

---

## 💬 交互命令

在 `denny` 交互界面中：

| 命令 | 说明 |
|------|------|
| `/quit` | 退出程序 |
| `/clear` | 清空当前对话历史 |
| `/memory` | 查看记忆状态 |
| `/rollback` | 回退长期记忆 |
| `/swarm <目标>` | 多 Agent 协作（例：`/swarm 分析项目并生成 README`） |
| `/webui` | 启动 Web 界面（http://127.0.0.1:8000） |
| `/help` | 显示帮助 |
| 3 次 Ctrl+C | 快速退出 |

---

## 🛠 内置工具

| 工具 | 说明 |
|------|------|
| `web_search` | 网页搜索（Bing / DuckDuckGo） |
| `read_file` / `write_file` / `edit_file` | 文件读写编辑 |
| `run_command` | 执行 shell 命令 |
| `list_dir` | 列出目录 |
| `calc` | 数学计算 |
| `read_image` / `image_process` | 图像读取与处理（需 Pillow / rembg） |
| `open_file` | 用系统默认程序打开文件 |
| `run_code` | 执行代码片段（Python/C/JS/TS/Java/Go） |
| `search_code` / `find_files` | 代码搜索与文件查找 |
| `embedded_doc` | MCU 固件 API 查询（内置函数签名库） |

---

## 🧠 记忆系统

三层记忆架构，持久化到 `.agent_memory/`：

1. **短期记忆** — 当前会话消息（最多 20 条，`/clear` 清空）
2. **长期记忆** — LLM 自动提取的事实与偏好，跨会话保留（`long_term.json`）
3. **情景记忆** — 对话摘要，保留最近 5 条，用于上下文回忆

系统会在每 10 条消息后自动提取事实，退出时保存对话摘要。

---

## 🌐 Web UI

```
denny → /webui
```

启动 FastAPI 服务（端口 8000），浏览器自动打开。支持：
- 多会话管理（侧边栏新建/切换/删除）
- SSE 流式实时输出（思考过程 + 文本 + 工具调用）
- 历史消息回显
- 清空 / 回退操作

---

## 🔌 MCP 扩展

在 `.config` 的 `MCP_SERVERS` 中配置：

### STDIO 模式

```json
"MCP_SERVERS": [
  {
    "command": "python",
    "args": ["path/to/mcp_server.py"]
  }
]
```

### HTTP 模式

```json
"MCP_SERVERS": [
  { "url": "http://127.0.0.1:8000" }
]
```

启动时自动连接 MCP 服务器并加载其工具，合并到工具列表。

---

## 🤝 代码调用

```python
import asyncio
from agent import Agent

async def main():
    agent = Agent()
    await agent.init_tools()
    result = await agent.run("列出当前目录文件")
    print(result)

asyncio.run(main())
```

---

## 📦 打包为 EXE

```bash
pip install pyinstaller
pyinstaller agent.spec
# 产物在 dist/agent.exe
```

---

## 📋 依赖

- `anthropic` — LLM SDK
- `mcp` — MCP 客户端
- `fastapi` + `uvicorn` — Web UI
- `httpx` — HTTP 客户端
- `colorama` — 终端着色
- `pyserial` — 串口通信
- 可选：`Pillow` + `rembg` — 图像处理

---

## 📄 License

MIT
