"""Read-only GitHub Issues actions for configured runner connections."""

import os
import re

import httpx

from aorch.contracts import StepResult

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def raise_github_error(response):
    if response.status_code == 401:
        raise ValueError("GitHub rejected the configured credential")
    message = ""
    if response.status_code == 403:
        try:
            message = str(response.json().get("message", "")).lower()
        except (AttributeError, ValueError):
            pass
    rate_limited = response.status_code == 429 or (
        response.status_code == 403
        and (response.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in message)
    )
    if rate_limited:
        retry_after = response.headers.get("Retry-After")
        suffix = f" Retry after {retry_after} seconds." if retry_after else ""
        raise ValueError(f"GitHub API rate limit exceeded.{suffix}")
    if response.status_code == 403:
        raise ValueError("GitHub denied the request; verify token permissions and repository access")
    response.raise_for_status()


def issue_to_item(issue):
    if "pull_request" in issue:
        return None
    return {
        "id": str(issue["number"]),
        "title": issue["title"],
        "description": issue.get("body") or "",
        "url": issue["html_url"],
        "labels": [label["name"] for label in issue.get("labels", []) if isinstance(label, dict)],
        "status": issue.get("state", "open"),
        "updated": issue.get("updated_at", ""),
    }


async def github_action(name, inputs, config, cancelled, transport=None):
    connection = config.connections.get(inputs["connection"])
    if connection is None:
        raise ValueError("Unknown GitHub connection")
    repository = inputs["repository"]
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("GitHub repository must use owner/name format")
    token_env = connection.get("token_env")
    token = os.environ.get(token_env, "") if token_env else ""
    if not token:
        raise ValueError("Missing GitHub credential")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        headers=headers,
        timeout=20,
        follow_redirects=False,
        transport=transport,
    ) as client:
        if cancelled.is_set():
            return StepResult(status="cancelled", summary="GitHub issue request stopped")
        if name == "github.get":
            response = await client.get(
                f"/repos/{repository}/issues/{inputs['issue']}"
            )
            raise_github_error(response)
            item = issue_to_item(response.json())
            if item is None:
                raise ValueError("Pull requests are not accepted as issue work items")
            return StepResult(
                status="succeeded",
                summary="GitHub issue loaded",
                outputs={"work_item": item},
            )

        items = []
        for page in range(1, 6):
            if cancelled.is_set():
                return StepResult(status="cancelled", summary="GitHub discovery stopped")
            params = {
                "state": inputs.get("state", "open"),
                "per_page": 100,
                "page": page,
            }
            if inputs.get("labels"):
                params["labels"] = ",".join(inputs["labels"])
            if inputs.get("assignee"):
                params["assignee"] = inputs["assignee"]
            response = await client.get(f"/repos/{repository}/issues", params=params)
            raise_github_error(response)
            batch = response.json()
            if not isinstance(batch, list):
                raise TypeError("GitHub returned an invalid issue page")
            items.extend(item for row in batch if (item := issue_to_item(row)) is not None)
            if len(batch) < 100:
                break
        else:
            raise ValueError("GitHub issue discovery reached the 500-item limit")
        return StepResult(
            status="succeeded",
            summary=f"Discovered {len(items)} GitHub issues",
            outputs={"work_items": items},
        )
