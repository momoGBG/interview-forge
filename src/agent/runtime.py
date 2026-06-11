"""轻量 Agent 运行时（参考 Claude Code 的工具循环思路，为弱模型加固）。

两个核心原语：
  1. call_json —— 强制结构化 JSON 输出 + 解析失败自动修复重试（弱模型可靠性关键）。
  2. agent_loop —— ReAct 式工具循环：模型选择调用工具或给最终答案，编排器执行工具、
     回灌观察，直到 final 或步数上限。工具集小、步数有界 → 在弱模型上稳定。

设计原则：确定性编排在外层（谁来出题、谁来评分由 Python 状态机决定），LLM 只在
被调用时做"窄任务"，因此即便模型智力一般，整体行为也可预测、可调试、可修复。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from ..infra.llm import LLM


def parse_json(text: str) -> dict | list | None:
    """从模型输出里抠出第一个 JSON 对象/数组，容错代码围栏与前后噪声。"""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, t, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def call_json(llm: LLM, system: str, user: str, *, max_tokens: int = 2500,
              temperature: float = 0.3, retries: int = 2) -> dict | list:
    """要 JSON，拿不到就把"你上次没给合法JSON"喂回去重试。"""
    sys_j = system + "\n\n严格只输出合法 JSON，不要任何额外文字或代码围栏。"
    msg = user
    last = ""
    for _ in range(retries + 1):
        raw = llm.chat(sys_j, msg, max_tokens=max_tokens, temperature=temperature,
                       think=False)
        parsed = parse_json(raw)
        if parsed is not None:
            return parsed
        last = raw[:200]
        msg = (user + f"\n\n[上次输出无法解析为 JSON，请只返回合法 JSON]"
                      f"\n上次输出片段：{last}")
    return {}


@dataclass
class Tool:
    name: str
    description: str            # 给模型看：什么时候用、输入是什么
    run: Callable[[dict], str]  # 执行：入参 dict → 观察字符串


def agent_loop(llm: LLM, system: str, task: str, tools: dict[str, Tool], *,
               max_steps: int = 3, max_tokens: int = 2000) -> dict:
    """ReAct 工具循环。模型每步返回：
      {"thought":"...", "action":"<工具名>|final", "action_input":{...}|<最终对象>}
    返回 action=='final' 时的 action_input。步数耗尽则返回最后一次 action_input 兜底。
    """
    tool_desc = "\n".join(f"- {t.name}: {t.description}" for t in tools.values())
    sys = (system + "\n\n你可以使用以下工具收集信息后再作答：\n" + tool_desc +
           "\n\n每一步只输出一个 JSON 对象：\n"
           '{"thought":"简述你的判断","action":"工具名或 final",'
           '"action_input": 工具入参对象 或 当 action=final 时的最终结果对象}\n'
           "需要更多信息就调用工具；信息够了就 action=final 给出最终结果。只输出 JSON。")
    transcript = f"任务：{task}"
    last_input: dict = {}
    for step in range(max_steps):
        out = call_json(llm, sys, transcript, max_tokens=max_tokens)
        if not isinstance(out, dict):
            break
        action = out.get("action", "final")
        last_input = out.get("action_input", out) if isinstance(
            out.get("action_input"), dict) else out
        if action == "final" or action not in tools:
            return out.get("action_input", out) if isinstance(
                out.get("action_input"), (dict, list)) else out
        # 执行工具，回灌观察
        try:
            obs = tools[action].run(out.get("action_input") or {})
        except Exception as e:  # noqa: BLE001
            obs = f"工具出错：{e}"
        transcript += (f"\n\n[第{step+1}步] 我调用了 {action}，观察到：\n{obs[:1500]}"
                       f"\n请基于观察继续（下一步或 final）。")
    return last_input
