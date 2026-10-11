"""OpenAI Responses agent loop with bounded local tools and explicit budgets."""

import asyncio
import json
import os
import time

import httpx

from aorch.contracts import StepResult
from aorch.runner.agents import RESULT_SCHEMA, credential_source, parse_agent_result
from aorch.runner.executor import environment
from aorch.runner.process import run_process


def function(name, description, properties):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def tools_for(profile):
    tools = [
        {
            "type": "function",
            "name": "finish",
            "description": "Return the final structured task result",
            "strict": True,
            "parameters": RESULT_SCHEMA,
        }
    ]
    if "read" in profile["tools"]:
        tools.append(
            function(
                "read",
                "Read a UTF-8 file inside the workspace",
                {"path": {"type": "string"}},
            )
        )
    if "write" in profile["tools"]:
        tools.append(
            function(
                "write",
                "Write a UTF-8 file inside the workspace",
                {"path": {"type": "string"}, "content": {"type": "string"}},
            )
        )
    if "command" in profile["tools"]:
        tools.append(
            function(
                "command",
                "Run an allowed executable with explicit arguments in the workspace",
                {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            )
        )
    return tools


def confined(workspace, relative):
    destination = (workspace / relative).resolve()
    if not destination.is_relative_to(workspace.resolve()) or relative.startswith("/"):
        raise ValueError("Path escapes workspace")
    return destination


async def invoke_tool(name, arguments, workspace, cancelled, config):
    from aorch.runner.process import log_context

    if cancelled.is_set():
        raise asyncio.CancelledError
    logger = log_context.get()
    if logger:
        logger("tool.started", {"message": f"API agent called {name}", "source": "agent"})
    if name == "read":
        path = confined(workspace, arguments["path"])
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("File exceeds read limit")
        return path.read_text()
    if name == "write":
        content = arguments["content"]
        if len(content.encode()) > 1024 * 1024:
            raise ValueError("File exceeds write limit")
        path = confined(workspace, arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return "Written"
    if name == "command":
        argv = arguments["argv"]
        if not argv or argv[0] not in config.command_allowlist:
            raise ValueError("Executable is not in the runner command allowlist")
        code, stdout, stderr = await run_process(
            argv, workspace, environment(config), "", 60, cancelled
        )
        return {"exit_code": code, "stdout": stdout, "stderr": stderr}
    raise ValueError(f"Unknown tool: {name}")


async def run_api_agent(
    profile, prompt, workspace, timeout, cancelled, config, transport=None
):
    source = credential_source("openai", profile, config)
    key = os.environ.get(source)
    if not key or not profile.get("model"):
        raise ValueError("API agent requires explicit model and credential")
    prices = (
        profile.get("input_price_per_million"),
        profile.get("output_price_per_million"),
    )
    if None in prices:
        raise ValueError("API budget requires configured input and output token prices")
    used_tokens, spent = 0, 0.0
    messages = [{"role": "user", "content": prompt}]
    tools = tools_for(profile)
    allowed = {tool["name"] for tool in tools}
    deadline = time.monotonic() + timeout

    async def loop():
        nonlocal used_tokens, spent
        async with httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {key}"},
            transport=transport,
        ) as client:
            for _ in range(profile["max_iterations"]):
                if cancelled.is_set():
                    raise asyncio.CancelledError
                remaining = profile["max_total_tokens"] - used_tokens
                output_limit = min(profile["max_output_tokens"], remaining)
                # Reserve conservatively for the entire serialized prompt plus output.
                input_estimate = 2 * (
                    len(json.dumps(messages).encode()) + len(json.dumps(tools).encode())
                )
                reserve = (
                    input_estimate * prices[0] + output_limit * prices[1]
                ) / 1_000_000
                if (
                    output_limit < 128
                    or input_estimate + output_limit > remaining
                    or spent + reserve > profile["max_budget_usd"]
                ):
                    return StepResult(
                        status="needs_attention",
                        summary="API token or cost budget exhausted",
                    )
                response = await client.post(
                    "/responses",
                    json={
                        "model": profile["model"],
                        "input": messages,
                        "tools": tools,
                        "tool_choice": "required",
                        "max_output_tokens": output_limit,
                        "store": False,
                    },
                    timeout=max(0.1, deadline - time.monotonic()),
                )
                response.raise_for_status()
                data = response.json()
                usage = data.get("usage", {})
                used_tokens += usage.get("total_tokens", 0)
                spent += (
                    usage.get("input_tokens", 0) * prices[0]
                    + usage.get("output_tokens", 0) * prices[1]
                ) / 1_000_000
                outputs = data.get("output", [])
                messages.extend(outputs)
                calls = [item for item in outputs if item["type"] == "function_call"]
                if not calls:
                    raise ValueError("API response did not call a tool")
                for call in calls:
                    if call["name"] not in allowed:
                        raise ValueError("Provider requested an unavailable tool")
                    arguments = json.loads(call["arguments"])
                    if call["name"] == "finish":
                        result = parse_agent_result(arguments)
                        result.outputs["api_usage"] = {
                            "tokens": used_tokens,
                            "estimated_cost_usd": spent,
                        }
                        return result
                    try:
                        output = await invoke_tool(
                            call["name"], arguments, workspace, cancelled, config
                        )
                    except (OSError, ValueError, KeyError) as error:
                        output = {"error": str(error)}
                    messages.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": json.dumps(output),
                        }
                    )
            return StepResult(
                status="needs_attention", summary="API iteration budget exhausted"
            )

    task = asyncio.create_task(loop())
    stop = asyncio.create_task(cancelled.wait())
    try:
        done, _ = await asyncio.wait(
            [task, stop], timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if stop in done:
            return StepResult(status="cancelled", summary="API agent stopped")
        if task not in done:
            return StepResult(status="failed", summary="API agent timeout")
        return task.result()
    finally:
        task.cancel()
        stop.cancel()
        await asyncio.gather(task, stop, return_exceptions=True)
