"""Run a persistent local demo with supervised engine and runner processes."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

from aorch.config.service import EngineConfig, load_service
from aorch.engine.storage import Store


def main():
    repo = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, default=repo / ".runtime/quick-start")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    state = args.directory.resolve()
    marker = state / ".quick-start"
    if state.exists() and any(state.iterdir()) and not marker.exists():
        parser.error("directory contains existing data; choose a new demo directory")
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"port {args.port} is in use; rerun with --port {args.port + 1}")

    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    marker.touch(mode=0o600)
    executable = str(Path(sys.executable).with_name("aorch"))
    subprocess.run(
        [executable, "init-local", str(repo / "examples/local-actions"), "--directory", str(state)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{args.port}"
    engine_path = state / "engine.yaml"
    config = yaml.safe_load(engine_path.read_text())
    config.update(host="127.0.0.1", port=args.port, public_base_url=url)
    engine_path.write_text(yaml.safe_dump(config))
    settings = load_service(engine_path, EngineConfig)
    store = Store(settings.database)
    store.local_auth_user()

    processes = []
    logs = []

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for name, command in (
            ("engine", [executable, "engine", str(engine_path), "--local-auth"]),
            ("runner", [executable, "runner", str(state / "runner.yaml"), "--engine-url", url]),
        ):
            if name == "runner":
                deadline = time.monotonic() + 35
                waiting = False
                while True:
                    with store.read_transaction() as db:
                        row = db.execute("SELECT heartbeat FROM runners WHERE id='local'").fetchone()
                    if row is None or row["heartbeat"] is None or time.time() - row["heartbeat"] >= 30:
                        break
                    if not waiting:
                        print("Waiting for the previous demo runner session to expire...", flush=True)
                        waiting = True
                    if processes[0].poll() is not None:
                        raise RuntimeError(f"Engine exited. Read {state / 'engine.log'}")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Another local runner is still connected. Stop it before restarting the demo.")
                    time.sleep(0.2)
            log_path = state / f"{name}.log"
            log = log_path.open("a")
            log_path.chmod(0o600)
            logs.append(log)
            process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
            processes.append(process)
            if name == "engine":
                deadline = time.monotonic() + 30
                with httpx.Client(timeout=1, trust_env=False) as client:
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError(f"Engine exited. Read {log_path}")
                        try:
                            if client.get(f"{url}/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"Engine did not become ready. Read {log_path}")
                        time.sleep(0.2)

        run_id = store.enqueue("local-actions", "commands")
        print(f"\nRunestra is running at {url}", flush=True)
        print("Open the page, choose Continue locally, then local-actions > commands.", flush=True)
        print(f"The sample run {run_id} is queued automatically. Waiting for its result...", flush=True)
        print(f"Data and logs: {state}\nPress Ctrl+C to stop both processes.", flush=True)
        deadline = time.monotonic() + 60
        reported = False
        while True:
            for name, process in zip(("engine", "runner"), processes, strict=True):
                if process.poll() is not None:
                    raise RuntimeError(f"{name.capitalize()} exited. Read {state / (name + '.log')}")
            if not reported:
                run = store.get_run(run_id)
                if run["status"] == "succeeded":
                    print("Sample succeeded: both steps ran. You can now explore the browser.", flush=True)
                    reported = True
                elif run["status"] in {"failed", "cancelled", "needs_attention", "reconciling"}:
                    raise RuntimeError(f"Sample ended with {run['status']}. Inspect the run in the browser.")
                elif time.monotonic() >= deadline:
                    raise RuntimeError("Sample did not complete within 60 seconds. Inspect the demo logs.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping the local demo. Its data is kept for the next start.", flush=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
