# HNS - 智能体系统

基于 LLM 的 Python 智能体，支持**本地工具调用 / MCP 扩展 / Skills 技能 / 三层记忆系统**。

## 项目文件

```
F:\HNS\
├── agent.py            # 智能体核心（含记忆系统 + 工具调用）
├── swarm_agent.py      # 群智能体（多 Agent 协作）
├── ymodem_debug.py     # YModem 调试工具
├── ymodem_gui.py       # YModem GUI 界面
├── ymodem_send.py      # YModem 发送工具
├── .config             # API 配置（不提交）
├── .config.example     # 配置模板
├── .gitignore
└── README.md
```

## 快速开始

```bash
pip install anthropic mcp langchain openai

# 编辑 .config 配置 API Key 和 Base URL
# 运行
python agent.py
```

## 核心功能

| 功能 | 说明 |
|------|------|
| **工具调用** | 搜索、文件读写、命令执行、计算等内置工具 |
| **MCP 扩展** | 支持挂载任意 MCP 服务器扩展能力 |
| **Skills** | JSON 定义的自定义技能插件 |
| **三层记忆** | 短期记忆 + 长期记忆（持久化）+ 情景记忆 |
| **群智能体** | 多 Agent 协同工作（swarm_agent.py） |

## 记忆系统

- **短期记忆** — 当前会话上下文（最多 20 条）
- **长期记忆** — 跨会话持久化的事实与偏好（`.agent_memory/long_term.json`）
- **情景记忆** — 历史对话摘要（保留最近 5 条）

聊天中输入 `memory` 查看记忆状态，输入 `clear` 清空短期记忆。

## 代码调用

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
