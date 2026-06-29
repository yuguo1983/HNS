"""
Denny Agent WebUI - FastAPI server with SSE streaming.
Run with: python webui.py
"""

import os
import sys
import json
import uuid
import asyncio
import threading
import webbrowser
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

import agent as agent_module
from agent import Agent, TOOL_HANDLERS, _content_block_to_dict

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鍖呮墦鍚庝娇鐢?sys._MEIPASS 璺緞
if hasattr(sys, "_MEIPASS"):
    _static_dir = os.path.join(sys._MEIPASS, "webui/static")
else:
    # 基于 webui.py 所在目录定位，避免 pip 安装后 cwd 不对找不到 static
    _static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "static")
app.mount("/webui/static", StaticFiles(directory=_static_dir), name="static")

# sessions: session_id -> {"agent": Agent, "initialized": bool, "lock": asyncio.Lock}
sessions: dict = {}
_sessions_lock = asyncio.Lock()


def _make_agent() -> Agent:
    return Agent()


def _load_mcp_servers() -> list:
    """浠庨厤缃姞杞?MCP 鏈嶅姟鍣ㄥ垪琛?"""
    servers: list = []
    config_str = os.getenv("MCP_SERVERS", "[]")
    try:
        parsed = json.loads(config_str)
        if isinstance(parsed, list):
            servers = parsed
    except Exception as e:
        print(f"  [!] MCP_SERVERS 瑙ｆ瀽澶辫触: {e}")

    # 濡傛灉閰嶇疆涓虹┖锛屽皾璇曟壂鎻忔牴鐩綍 mcp_servers
    if not servers:
        mcp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_servers")
        if os.path.isdir(mcp_dir):
            for entry in sorted(os.listdir(mcp_dir)):
                full = os.path.join(mcp_dir, entry)
                if os.path.isdir(full):
                    py_files = [f for f in os.listdir(full) if f.endswith(".py") and f != "__init__.py"]
                    if py_files:
                        servers.append({
                            "command": sys.executable,
                            "args": [os.path.join(full, py_files[0])],
                        })
                        break
    return servers


_MCP_SERVERS = _load_mcp_servers()


async def _ensure_session_initialized(session: dict):
    """寤惰繜鍒濆鍖栧伐鍏凤紝閬垮厤鍒涘缓浼氳瘽鏃跺欢杩?    """
    if session.get("initialized"):
        return
    lock = session.get("lock")
    if lock is None:
        lock = asyncio.Lock()
        session["lock"] = lock
    async with lock:
        if session.get("initialized"):
            return
        try:
            if getattr(sys, 'frozen', False):
                base_dir = sys._MEIPASS
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            skill_dir = os.path.join(base_dir, "skills")
            await session["agent"].init_tools(servers=_MCP_SERVERS, skill_dir=skill_dir)
        except Exception as e:
            print(f"  [!] 宸ュ叿鍒濆鍖栧け璐? {e}")
        session["initialized"] = True


def get_or_create_session(session_id: Optional[str]) -> tuple[str, dict]:
    if not session_id:
        sid = uuid.uuid4().hex[:8]
        sessions[sid] = {"agent": _make_agent(), "initialized": False, "lock": asyncio.Lock()}
    elif session_id in sessions:
        sid = session_id
    else:
        sid = uuid.uuid4().hex[:8]
        sessions[sid] = {"agent": _make_agent(), "initialized": False, "lock": asyncio.Lock()}
    return sid, sessions[sid]


@app.get("/")
async def root():
    with open(os.path.join(_static_dir, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# --- Session APIs ---
@app.get("/api/sessions")
async def list_sessions():
    return {
        "sessions": [
            {"session_id": sid, "name": ""}
            for sid, s in sessions.items()
        ]
    }


@app.post("/api/sessions")
async def create_session():
    sid = uuid.uuid4().hex[:8]
    sessions[sid] = {"agent": _make_agent(), "initialized": False, "lock": asyncio.Lock()}
    return {"session_id": sid}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
    return {"status": "deleted"}


@app.get("/api/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    if session_id not in sessions:
        return {"messages": []}
    agent_obj = sessions[session_id]["agent"]
    return {"messages": agent_obj.memory.short_term if hasattr(agent_obj, "memory") and agent_obj.memory else []}


@app.post("/api/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    if session_id in sessions:
        sessions[session_id]["agent"].memory.clear_short_term()
    return {"status": "cleared"}


@app.post("/api/rollback/{session_id}")
async def rollback_session(session_id: str, steps: int = 1):
    if session_id in sessions:
        agent_obj = sessions[session_id]["agent"]
        if hasattr(agent_obj, "memory") and agent_obj.memory:
            st = agent_obj.memory.short_term
            cut = steps * 2
            agent_obj.memory.short_term = st[:-cut] if len(st) > cut else []
    return {"status": "rolled back"}


# --- SSE Chat API (蹇呴』鍦?/api/chat/{session_id} 涔嬪墠瀹氫箟) ---
@app.get("/api/sse/chat")
async def sse_chat(message: str = "", session_id: str = ""):
    sid, session = get_or_create_session(session_id) if session_id else (uuid.uuid4().hex[:8], None)

    if not session:
        sid = uuid.uuid4().hex[:8]
        session = {"agent": _make_agent(), "initialized": False, "lock": asyncio.Lock()}
        sessions[sid] = session

    async def event_generator():
        # 用队列桥接：agent.run 的回调把 SSE 事件推入队列，主循环再 yield
        queue: asyncio.Queue = asyncio.Queue()
        DONE = object()

        try:
            yield f"event: session_id\ndata: {json.dumps({'session_id': sid})}\n\n"
            yield f"event: status\ndata: {json.dumps({'phase': 'thinking', 'message': ''})}\n\n"

            await _ensure_session_initialized(session)

            async def on_chunk(chunk: str):
                await queue.put(f"event: delta\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n")

            async def on_event(name: str, payload):
                await queue.put(f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n")

            async def run_task():
                try:
                    response = await session["agent"].run(message, on_chunk=on_chunk, on_event=on_event)
                    # 兜底：完整文本也发一次 message，便于前端在没收到 delta 时仍有内容
                    await queue.put(f"event: message\ndata: {json.dumps({'content': response}, ensure_ascii=False)}\n\n")
                except Exception as e:
                    print(f"  [webui] chat error: {e}")
                    await queue.put(f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n")
                finally:
                    await queue.put(DONE)

            task = asyncio.create_task(run_task())

            # 主循环：边消费队列边 yield，实现真正流式
            while True:
                item = await queue.get()
                if item is DONE:
                    break
                yield item

            await task
            yield f"event: done\ndata: {json.dumps({'session_id': sid})}\n\n"

        except Exception as e:
            err_msg = str(e)
            print(f"  [webui] chat error: {err_msg}")
            yield f"event: error\ndata: {json.dumps({'error': err_msg})}\n\n"
            yield f"event: done\ndata: {json.dumps({'session_id': sid})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --- Chat APIs ---
@app.post("/api/chat/{session_id}")
async def chat(request: Request, session_id: str):
    sid, session = get_or_create_session(session_id)
    body = await request.json()
    query = body.get("query", "")

    if not query:
        return {"error": "No query provided"}

    try:
        await _ensure_session_initialized(session)
        response = await session["agent"].run(query)
        return {
            "session_id": sid,
            "response": response,
            "messages": session["agent"].memory.short_term if hasattr(session["agent"], "memory") and session["agent"].memory else [],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/memory/{session_id}")
async def get_memory(session_id: str):
    if session_id not in sessions:
        return {"error": "Session not found"}
    agent_obj = sessions[session_id]["agent"]
    return {
        "messages": agent_obj.memory.short_term if hasattr(agent_obj, "memory") and agent_obj.memory else [],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "sessions": len(sessions)}


if __name__ == "__main__":
    print("=" * 56)
    print("  Denny Agent WebUI")
    print("  URL: http://localhost:8000")
    print(f"  MCP servers: {len(_MCP_SERVERS)}")
    print("=" * 56)

    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8000)
