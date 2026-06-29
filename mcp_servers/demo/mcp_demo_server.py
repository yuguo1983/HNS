"""MCP Demo Server - HTTP 模式"""
from datetime import datetime

from mcp.server.fastmcp import FastMCP


async def calculator(expression: str) -> str:
    """执行数学计算"""
    try:
        return str(eval(expression))
    except:
        return "error"


async def get_time() -> str:
    """获取当前时间"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def greet(name: str) -> str:
    """问候"""
    return f"Hello {name}"


async def echo(message: str) -> str:
    """回显"""
    return message


def main():
    # 使用 HTTP 模式运行
    server = FastMCP(
        name="mcp-demo",
        host="127.0.0.1",
        port=8000
    )
    server.add_tool(calculator)
    server.add_tool(get_time)
    server.add_tool(greet)
    server.add_tool(echo)
    
    # 使用 run_streamable_http_async 而不是 run()
    import asyncio
    asyncio.run(server.run_streamable_http_async())


if __name__ == "__main__":
    main()