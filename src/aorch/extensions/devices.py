"""Reserved simulator/emulator testing with explicit startup and cleanup."""

import asyncio
import hashlib
import json
import re
import shutil
import time

from aorch.contracts import Artifact, StepResult
from aorch.extensions.git import command
from aorch.runner.executor import environment, process_identity
from aorch.runner.process import stop_group


async def test_device(inputs, assignment, config, journal, cancelled, workspace):
    name = inputs["device"]
    if name not in assignment["step"]["resources"]:
        raise ValueError("Device action requires the matching resource reservation")
    device = config.devices[name]
    platform = device["platform"]
    root = workspace / "code" if (workspace / "code").is_dir() else workspace
    started, failed = False, None
    emulator_process = None
    report = workspace / f"{name}-report.txt"
    screenshot = workspace / f"{name}-screenshot.png"
    captured = []

    async def run(argv, cleanup=False):
        output = await command(
            argv,
            root,
            config,
            journal,
            assignment,
            asyncio.Event() if cleanup else cancelled,
            assignment["step"]["timeout_seconds"],
        )
        captured.append(output)
        return output

    try:
        if platform == "ios":
            if shutil.which("xcrun") is None:
                raise ValueError("xcrun is missing")
            identifier = device["id"]
            devices = json.loads(
                await run(["xcrun", "simctl", "list", "devices", "--json"])
            )
            match = next(
                (
                    entry
                    for entries in devices["devices"].values()
                    for entry in entries
                    if entry["udid"] == identifier and entry.get("isAvailable")
                ),
                None,
            )
            if match is None:
                raise ValueError("Configured iOS simulator is unavailable")
            if match["state"] != "Booted":
                journal.resource_started(
                    assignment["attempt_id"], name, "ios", identifier
                )
                started = True
                await run(["xcrun", "simctl", "boot", identifier])
            await run(["xcrun", "simctl", "bootstatus", identifier, "-b"])
        elif platform == "android":
            if shutil.which("adb") is None:
                raise ValueError("adb is missing")
            identifier = device["id"]
            listing = await run(["adb", "devices"])
            if f"{identifier}\tdevice" not in listing:
                emulator = device.get("emulator") or shutil.which("emulator")
                if (
                    not emulator
                    or not device.get("avd")
                    or not re.fullmatch(r"emulator-[0-9]+", identifier)
                ):
                    raise ValueError(
                        "Missing emulator executable, AVD or valid emulator serial"
                    )
                port = int(identifier.split("-")[1])
                if port < 5554 or port > 5682 or port % 2:
                    raise ValueError(
                        "Android emulator console port must be even and between 5554 and 5682"
                    )
                journal.resource_started(
                    assignment["attempt_id"], name, "android", identifier
                )
                emulator_process = await asyncio.create_subprocess_exec(
                    emulator,
                    "-avd",
                    device["avd"],
                    "-port",
                    str(port),
                    "-no-window",
                    "-no-audio",
                    "-no-boot-anim",
                    env=environment(config),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                journal.resource_started(
                    assignment["attempt_id"],
                    name,
                    "android",
                    identifier,
                    emulator_process.pid,
                    process_identity(emulator_process.pid),
                )
                started = True
            await run(["adb", "-s", identifier, "wait-for-device"])
            deadline = time.monotonic() + assignment["step"]["timeout_seconds"]
            while (
                await run(
                    ["adb", "-s", identifier, "shell", "getprop", "sys.boot_completed"]
                )
                != "1"
            ):
                if time.monotonic() >= deadline or cancelled.is_set():
                    raise ValueError("Android emulator boot timed out or was cancelled")
                await asyncio.sleep(0.5)
        else:
            raise ValueError("Device platform must be ios or android")
        commands = inputs["commands"]
        if not commands:
            raise ValueError("Device verification requires test commands")
        for argv in commands:
            # The whole argument @device is replaced, never a shell substring.
            await run([identifier if value == "@device" else value for value in argv])
        if platform == "ios":
            await run(
                ["xcrun", "simctl", "io", identifier, "screenshot", str(screenshot)]
            )
        else:
            remote_path = "/sdcard/aorch-screenshot.png"
            await run(
                ["adb", "-s", identifier, "shell", "screencap", "-p", remote_path]
            )
            await run(["adb", "-s", identifier, "pull", remote_path, str(screenshot)])
            await run(["adb", "-s", identifier, "shell", "rm", remote_path])
    except (OSError, ValueError, TimeoutError, asyncio.CancelledError) as error:
        failed = str(error) or "Device test cancelled"
    finally:
        if started:
            try:
                if platform == "ios":
                    await run(
                        ["xcrun", "simctl", "shutdown", device["id"]], cleanup=True
                    )
                elif emulator_process:
                    await stop_group(emulator_process)
                journal.resource_stopped(assignment["attempt_id"], name)
            except (OSError, ValueError, TimeoutError):
                return StepResult(
                    status="needs_attention",
                    summary="Device shutdown uncertain; reservation requires reconciliation",
                    error="ResourceUncertain",
                )
    report.write_text(
        "\n".join(captured) + ("\nERROR: " + failed if failed else "\nTests passed")
    )
    artifacts = []
    for path in (report, screenshot):
        if path.exists():
            data = path.read_bytes()
            artifacts.append(
                Artifact(
                    path=str(path.relative_to(workspace)),
                    sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                )
            )
    return StepResult(
        status="failed" if failed else "succeeded",
        summary=failed or "Device tests passed",
        artifacts=artifacts,
    )
