# AtomCode Agent - 最精简智能体

支持 **本地工具 / MCP 工具 / Skills / LLM 对话 / 记忆系统** 的 Python 智能体。

## 依赖

```bash
pip install anthropic mcp langchain openai
```

## 快速开始

```bash
# 编辑 .config 配置文件
# 设置 API Key 和 Base URL

# 运行
python agent.py
```

## 内置工具

| 工具 | 说明 |
|------|------|
| `web_search` | DuckDuckGo 搜索 |
| `read_file` | 读取文件内容 |
| `write_file` | 创建/覆盖文件 |
| `run_command` | 执行 shell 命令 |
| `list_dir` | 列出目录 |
| `calc` | 数学计算 |

## 记忆系统

三层记忆架构：

### 1. 短期记忆 (Short-term)
- 当前会话的完整对话历史
- 受上下文窗口限制，最多保留 20 条消息
- 对话结束后自动清空

### 2. 长期记忆 (Long-term)
- 从对话中自动提取的事实和偏好
- 跨会话持久化存储在 `.agent_memory/long_term.json`
- 每次对话结束时自动提取新信息
- 对话开始时自动加载到系统提示中

### 3. 情景记忆 (Episodic)
- 已结束对话的 LLM 摘要
- 最近 5 条摘要保留在记忆中
- 帮助 Agent 回顾之前的对话内容

### 交互命令

在聊天模式下：
- `memory` — 查看当前记忆状态
- `clear` — 清空短期记忆（开始新对话）

## 扩展 MCP 工具

在 `main()` 中添加 MCP 服务器配置：

```python
mcp_servers = [
    # 文件系统
    {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\YourName"]},
    # 自定义 Python MCP Server
    {"command": "python", "args": ["-m", "my_mcp_server"]},
]
```

## 扩展 Skills

在 `skills/` 目录添加 JSON 文件，格式：

```json
{
  "tools": [
    {
      "name": "my_skill_tool",
      "description": "描述",
      "input_schema": {
        "type": "object",
        "properties": {
          "param": {"type": "string"}
        },
        "required": ["param"]
      }
    }
  ]
}
```

## 架构

```
Agent.run()  →  LLM 调用 →  有工具调用？
                    ↓ 是          ↓ 是
                纯文本回复    执行工具 → 结果回传 LLM
                    ↓ 否          ↓
                  返回结果      (循环)
                              ↓ 结束
                    提取事实 → 保存到长期记忆
                              ↓
                    清空短期记忆
```

## 使用代码嵌入

```python
import asyncio
from agent import Agent

async def main():
    agent = Agent(model="claude-sonnet-4-5-20250929")
    await agent.init_tools()
    result = await agent.run("帮我列出当前目录的文件并计算 2**10")
    print(result)

asyncio.run(main())
```

## 文件结构

```
F:\HNS\
├── agent.py          # 智能体核心（含记忆系统）
├── .config           # API 配置（不提交 git）
├── .config.example   # 配置模板
├── .agent_memory/    # 长期记忆存储（自动生成）
│   └── long_term.json
└── .gitignore        # 排除配置和记忆文件
```
