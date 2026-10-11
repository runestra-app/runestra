"""Single-engine scheduler loop; provider execution remains on runners."""

import asyncio
import json
import logging

import httpx

from aorch.engine.storage import Store

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, store: Store, refresh_project=None, poll_seconds: float = 1, public_base_url=""):
        if poll_seconds <= 0:
            raise ValueError("Scheduler poll interval must be positive")
        self.store = store
        self.refresh_project = refresh_project
        self.poll_seconds = poll_seconds
        self.public_base_url = public_base_url.rstrip("/")

    async def deliver_notifications(self):
        for item in await asyncio.to_thread(self.store.pending_notifications):
            payload = json.loads(item["payload"])
            payload["url"] = self.public_base_url + "/"
            text = f"Runestra: {payload['project']} / {payload['workflow']} is {payload['event']} (run {payload['run_id'][:8]}). {payload['url']}"
            targets = await asyncio.to_thread(self.store.notification_targets_for, item["organization_id"])
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    for kind, url in targets:
                        response = await client.post(url, json=({"text": text} if kind == "slack" else payload))
                        response.raise_for_status()
                if targets:
                    await asyncio.to_thread(self.store.notification_result, item["id"])
            except (httpx.HTTPError, ValueError) as error:
                await asyncio.to_thread(self.store.notification_result, item["id"], type(error).__name__)

    def tick(self):
        self.store.expire()
        self.store.expire_approvals()
        self.store.prune_runs()
        self.store.prune_webhook_deliveries()
        self.store.prune_notifications()
        if self.refresh_project:
            for project in self.store.due_projects():
                row = next((item for item in self.store.overview()["projects"] if item["name"] == project), None)
                if row and row["source_type"] == "git":
                    result = self.refresh_project(project)
                    if result.get("status") != "ready":
                        self.store.defer_project(project)
        return self.store.tick()

    async def tick_async(self):
        await asyncio.to_thread(self.store.expire)
        await asyncio.to_thread(self.store.expire_approvals)
        await asyncio.to_thread(self.store.prune_runs)
        await asyncio.to_thread(self.store.prune_webhook_deliveries)
        await asyncio.to_thread(self.store.prune_notifications)
        await self.deliver_notifications()
        if self.refresh_project:
            due = await asyncio.to_thread(self.store.due_projects)
            overview = await asyncio.to_thread(self.store.overview)
            for project in due:
                row = next((item for item in overview["projects"] if item["name"] == project), None)
                if row and row["source_type"] == "git":
                    result = self.refresh_project(project)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if result.get("status") != "ready":
                        await asyncio.to_thread(self.store.defer_project, project)
        return await asyncio.to_thread(self.store.tick)

    async def serve(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await self.tick_async()
            except Exception:
                logger.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
