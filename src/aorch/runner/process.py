"""Bounded subprocess capture and process group cancellation."""

import asyncio
import os
import signal
from contextvars import ContextVar

log_context = ContextVar("aorch_log_context", default=None)
log_suppressed = ContextVar("aorch_log_suppressed", default=False)


async def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        pass
    # The parent may have exited while descendants remain alive.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def run_process(
    argv, cwd, environment, stdin, timeout, cancelled, on_start=None, limit=256 * 1024
):
    output_exceeded = asyncio.Event()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    if on_start:
        on_start(process.pid)

    async def capture(stream):
        chunks, size = [], 0
        while chunk := await stream.read(8192):
            remaining = max(0, limit - size)
            logger = None if log_suppressed.get() else log_context.get()
            if logger and remaining:
                logger("stream", {
                    "stream": "stdout" if stream is process.stdout else "stderr",
                    "message": chunk[:remaining].decode(errors="replace"),
                })
            if remaining:
                chunks.append(chunk[:remaining])
            if len(chunk) > remaining:
                output_exceeded.set()
            size += len(chunk)
        return b"".join(chunks).decode(errors="replace")

    async def feed():
        try:
            process.stdin.write(stdin.encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    async def collect():
        _, stdout, stderr, code = await asyncio.gather(
            feed(), capture(process.stdout), capture(process.stderr), process.wait()
        )
        return code, stdout, stderr

    execution = asyncio.create_task(collect())
    cancellation = asyncio.create_task(cancelled.wait())
    overflow = asyncio.create_task(output_exceeded.wait())
    try:
        done, _ = await asyncio.wait(
            [execution, cancellation, overflow],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation in done:
            raise asyncio.CancelledError
        if overflow in done or output_exceeded.is_set():
            raise ValueError(f"Subprocess output exceeded the {limit}-byte limit")
        if execution not in done:
            raise TimeoutError("Subprocess timeout")
        result = execution.result()
        await stop_group(process)
        return result
    finally:
        await stop_group(process)
        execution.cancel()
        cancellation.cancel()
        overflow.cancel()
        await asyncio.gather(execution, cancellation, overflow, return_exceptions=True)
