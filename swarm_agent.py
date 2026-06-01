"""
Swarm Agent - 多Agent协作系统
主Agent制定方案 → 分配并行子任务 → 子Agent多线程执行 → 主Agent汇总结果

用法:
    from swarm_agent import OrchestratorAgent
    agent = OrchestratorAgent()
    result = await agent.orchestrate("你的目标")
"""
import asyncio
import json
import os
import time
from typing import Optional
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from agent import (
    Agent, TOOLS, TOOL_HANDLERS,
    _content_block_to_dict,
    load_mcp_tools, load_skills,
)


# ═══════════════════════════════════════════════════════
#  数据类型
# ═══════════════════════════════════════════════════════

@dataclass
class SubTask:
    """子任务定义"""
    id: str
    name: str
    description: str
    context: str = ""


@dataclass
class SubTaskResult:
    """子任务执行结果"""
    id: str
    name: str
    success: bool
    output: str
    error: str = ""
    duration: float = 0.0


# ═══════════════════════════════════════════════════════
#  子Agent - 轻量级执行者
# ═══════════════════════════════════════════════════════

class SubAgent:
    """
    子Agent - 轻量级执行者
    接收明确的任务指令，独立执行，返回结果
    无长期记忆，执行完毕即销毁
    """

    def __init__(
        self,
        task: SubTask,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        skill_dir: str = "skills",
    ):
        self.task = task
        self.model = model or os.getenv("ANTHROPIC_MODEL", "deepseek-v4-flash")
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.skill_dir = skill_dir

        self.client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        )
        self.tools: list = []

    async def _init_tools(self):
        """初始化子Agent的工具集（内置工具 + Skills）"""
        self.tools = list(TOOLS)
        skills = load_skills(self.skill_dir)
        self.tools.extend(skills)

    async def execute(self) -> SubTaskResult:
        """执行子任务，返回结果"""
        start_time = time.time()
        try:
            await self._init_tools()

            system_prompt = (
                "你是 Denny Agent 的子任务执行者。\n"
                "你的任务是严格按照分配的指令执行，不要擅自扩大范围。\n"
                "调用适当的工具来完成任务，完成后给出清晰的总结。\n"
                "注意：你只负责执行分配给你的子任务，不要做额外的事。\n"
            )

            messages = [
                {
                    "role": "user",
                    "content": f"""## 任务描述
{self.task.description}

## 上下文
{self.task.context}

请执行上述任务。完成后，先用 ===RESULT=== 包裹你的最终输出结果。
格式：
===RESULT===
[你的最终成果]
===RESULT====""",
                }
            ]

            max_iter = 20
            for _ in range(max_iter):
                resp = await self.client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=messages,
                    tools=self.tools,
                    max_tokens=8192,
                )

                text_blocks = [b for b in resp.content if b.type == "text"]
                partial_text = "".join(b.text for b in text_blocks)

                if resp.stop_reason == "end_turn":
                    messages.append({"role": "assistant", "content": partial_text})
                    duration = time.time() - start_time
                    return SubTaskResult(
                        id=self.task.id,
                        name=self.task.name,
                        success=True,
                        output=partial_text,
                        duration=duration,
                    )

                if resp.stop_reason == "tool_use":
                    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
                    assistant_content = [
                        _content_block_to_dict(b) for b in resp.content
                    ]
                    messages.append({"role": "assistant", "content": assistant_content})

                    tool_results = []
                    for block in tool_blocks:
                        tool_name = block.name
                        tool_input = block.input or {}
                        try:
                            handler = TOOL_HANDLERS.get(tool_name)
                            if handler:
                                result = handler(**tool_input) if tool_input else handler()
                            else:
                                result = "[工具已调用]"
                        except Exception as e:
                            result = f"[错误] {e}"
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(result),
                            }
                        )

                    if tool_results:
                        messages.append({"role": "user", "content": tool_results})
                    continue

                if resp.stop_reason == "max_tokens":
                    messages.append({"role": "assistant", "content": partial_text})
                    messages.append(
                        {
                            "role": "user",
                            "content": "请继续完成上面未完成的内容。",
                        }
                    )
                    continue

                break

            duration = time.time() - start_time
            return SubTaskResult(
                id=self.task.id,
                name=self.task.name,
                success=False,
                output="",
                error="超出最大迭代次数",
                duration=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            return SubTaskResult(
                id=self.task.id,
                name=self.task.name,
                success=False,
                output="",
                error=str(e),
                duration=duration,
            )


# ═══════════════════════════════════════════════════════
#  主Agent - 方案制定 + 任务分配 + 结果整合
# ═══════════════════════════════════════════════════════

class OrchestratorAgent(Agent):
    """
    主Agent（继承自 Agent）
    工作流程:
      1. 分析目标 → 输出 JSON 子任务计划
      2. 并行派发所有子任务给 SubAgent
      3. 收集结果 → 合成最终答案
    """

    def __init__(self, model=None, api_key=None, max_workers=5):
        super().__init__(model, api_key)
        self.max_workers = max_workers  # 最大并行子Agent数

    async def orchestrate(self, goal: str) -> str:
        """
        多Agent协作执行入口
        1. 规划 → 2. 并行执行 → 3. 整合结果
        """
        print(f"\n{'='*60}")
        print(f"[SWARM] 多Agent协作模式启动")
        print(f"[目标] {goal}")
        print(f"{'='*60}")

        # ── 第1步：规划任务分解 ──
        print(f"\n[1/3] 任务规划中...")
        plan = await self._plan(goal)

        if not plan or len(plan) == 0:
            print(f"  [!] 无需分解，直接由主Agent执行")
            # 回退到单Agent模式
            return await self.run(goal)

        print(f"  [+] 分解为 {len(plan)} 个子任务:")
        for t in plan:
            print(f"     [{t.id}] {t.name}")

        # ── 第2步：并行执行 ──
        print(f"\n[2/3] 并行执行 {len(plan)} 个子任务...")
        results = await self._execute_parallel(plan)

        print(f"\n  [结果] 执行结果:")
        success_count = 0
        for r in results:
            status = "[OK]" if r.success else "[FAIL]"
            print(f"     {status} [{r.id}] {r.name} ({r.duration:.1f}s)")
            if r.success:
                success_count += 1
        print(f"  [统计] 成功率: {success_count}/{len(results)}")

        # ── 第3步：整合结果 ──
        print(f"\n[3/3] 整合最终结果...")
        final = await self._synthesize(goal, results)

        return final

    async def _plan(self, goal: str) -> list[SubTask]:
        """让主Agent LLM 将目标分解为子任务列表"""
        plan_prompt = (
            "你是一个任务规划专家。请将以下目标分解为多个可并行执行的子任务。\n\n"
            f"目标: {goal}\n\n"
            "请输出一个 JSON 数组，每个元素包含:\n"
            "- id: 唯一标识 (如 task_1, task_2, ...)\n"
            "- name: 简短任务名 (中文)\n"
            "- description: 详细的执行指令，包括要使用什么工具、做什么事\n"
            "- context: 提供给子Agent的额外上下文\n\n"
            "要求:\n"
            "1. 子任务之间不要有依赖关系（如果A依赖B，合并为一个任务）\n"
            "2. 每个子任务包含足够信息让子Agent独立完成\n"
            "3. 如果任务不需要分解，输出空数组 []\n"
            "4. 最多不超过 10 个子任务\n"
            "5. 只输出 JSON，不要有其他文字\n"
        )

        try:
            resp = await self.client.messages.create(
                model=self.model,
                system="你是一个任务分解专家。只输出JSON，不输出其他文字。",
                messages=[{"role": "user", "content": plan_prompt}],
                max_tokens=4096,
            )

            text = "".join(b.text for b in resp.content if b.type == "text")

            # 提取 JSON（兼容 ```json 包裹或纯文本）
            json_text = text.strip()
            if "```json" in json_text:
                json_text = json_text.split("```json")[1].split("```")[0].strip()
            elif "```" in json_text:
                json_text = json_text.split("```")[1].split("```")[0].strip()

            tasks_data = json.loads(json_text)

            if not isinstance(tasks_data, list) or len(tasks_data) == 0:
                return []

            return [SubTask(**t) for t in tasks_data]

        except Exception as e:
            print(f"  ⚠️ 规划失败: {e}")
            return []

    async def _execute_parallel(self, tasks: list[SubTask]) -> list[SubTaskResult]:
        """并行执行所有子任务（asyncio.gather）"""
        sem = asyncio.Semaphore(self.max_workers)

        async def _run_one(task: SubTask) -> SubTaskResult:
            async with sem:
                agent = SubAgent(
                    task=task,
                    model=self.model,
                    api_key=self.client.api_key,
                )
                return await agent.execute()

        # 全部并行启动
        coros = [_run_one(t) for t in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        final_results = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                final_results.append(
                    SubTaskResult(
                        id=tasks[i].id,
                        name=tasks[i].name,
                        success=False,
                        output="",
                        error=str(r),
                    )
                )
            else:
                final_results.append(r)

        return final_results

    async def _synthesize(
        self, goal: str, results: list[SubTaskResult]
    ) -> str:
        """整合所有子任务的结果"""
        results_text = []
        for r in results:
            status = "✅ 成功" if r.success else "❌ 失败"
            results_text.append(
                f"## [{r.id}] {r.name} ({status})\n"
                f"耗时: {r.duration:.1f}s\n"
                f"{'输出:' if r.success else '错误:'}\n"
                f"{r.output if r.success else r.error}\n"
            )

        synthesis_prompt = (
            f"## 原始目标\n{goal}\n\n"
            f"## 各子任务执行结果\n\n"
            f"{chr(10).join(results_text)}\n\n"
            "请根据以上所有子任务的执行结果，整合成一份完整的最终答案给用户。\n"
            "要求：\n"
            "1. 覆盖所有子任务的成果\n"
            "2. 逻辑连贯，语言自然\n"
            "3. 如果有子任务失败，说明失败原因并给出建议\n"
        )

        try:
            resp = await self.client.messages.create(
                model=self.model,
                system=self.system_prompt,
                messages=[{"role": "user", "content": synthesis_prompt}],
                max_tokens=8192,
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text
        except Exception as e:
            # 回退：直接拼接结果
            parts = [f"# 多Agent协作结果\n\n## 原始目标\n{goal}\n"]
            for r in results:
                parts.append(
                    f"## {r.name} [{'✅' if r.success else '❌'}]\n"
                    f"{r.output if r.success else f'错误: {r.error}'}\n"
                )
            return "\n\n".join(parts)
