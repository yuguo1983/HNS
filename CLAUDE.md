# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Denny Agent — a single-file Python AI agent (~900 lines) with built-in tools, MCP integration, skills system, and a three-layer memory architecture. Uses the Anthropic SDK pointed at a DeepSeek proxy.

## Commands

```bash
# Run the agent interactively
python agent.py

# Package as standalone Windows EXE
pyinstaller agent.spec
```

## Architecture

### Config loading
`.config` (JSON, gitignored) is loaded at import time by `_load_config()`. Keys are set as environment variables: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`. Falls back to env vars if no config file found.

### Agent class (`Agent`)
- Constructor: accepts optional `model` and `api_key`; falls back to env vars, then `deepseek-v4-flash`
- `init_tools()`: aggregates built-in tools + MCP servers + skills from `skills/` directory
- `run()`: the core loop — sends `messages` to the LLM, handles `tool_use` stop reason by dispatching to `TOOL_HANDLERS`, returns when `end_turn`. Auto-extracts facts to long-term memory every 10+ messages.
- `chat()`: interactive REPL with `quit`/`clear`/`memory` commands

### Tool registration
Tools are defined in the `TOOLS` list (schema for the API) and registered via the `@_register("name")` decorator into `TOOL_HANDLERS`. Built-in tools: `web_search`, `read_file`, `write_file`, `edit_file`, `run_command`, `list_dir`, `calc`, `read_image`, `open_file`, `image_process`, `search_code`, `find_files`.

### Memory system (`Memory` class)
Three layers persisted to `.agent_memory/long_term.json`:
1. **Short-term** — current conversation messages (max 20, cleared on `clear` command)
2. **Long-term** — facts and preferences extracted by LLM at end of conversation, loaded into system prompt on startup
3. **Episodic** — LLM-generated summaries of past conversations (keeps last 5)

### MCP integration
`load_mcp_tools()` connects to MCP stdio servers, discovers their tools, and adds them to the agent's tool list. Configure servers in `main()` as a list of `{"command": ..., "args": [...]}` dicts.

### Skills system
`load_skills()` scans `skills/` for subdirectories containing `SPEC.md` files. Each becomes a tool whose description comes from the markdown content. Currently includes `analyze_compile` (reads and analyzes `compile.log`).

### Code execution (`run_code`)
Executes code snippets in Python, C, JavaScript, TypeScript, Java, Go via temporary files. C and Java require compilation. Configured in `LANG_CONFIG`.

### MCU firmware lookup (`embedded_doc`)
Built-in dictionary of MCU function signatures (`_EMBEDDED_FUNCTIONS`) and keyword索引 (`_KEYWORDS`) for looking up firmware APIs by name or concept.

## Dependencies

```bash
pip install anthropic mcp
# Optional: for image tools
pip install Pillow rembg
```
