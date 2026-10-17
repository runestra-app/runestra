"""Jira Cloud discovery, issue transitions, and clarification comments."""

import hashlib
import json
from urllib.parse import quote

import httpx

from aorch.contracts import StepResult
from aorch.extensions.ledger import EffectLedger


def adf(text):
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def plain_text(value):
    if isinstance(value, dict):
        return str(value.get("text", "")) + " ".join(
            plain_text(child) for child in value.get("content", [])
        )
    return ""


async def comments(client, key):
    rows, start = [], 0
    for _ in range(100):
        response = await client.get(
            f"/rest/api/3/issue/{quote(key, safe='')}/comment",
            params={"startAt": start, "maxResults": 100},
        )
        response.raise_for_status()
        data = response.json()
        batch = data.get("comments", [])
        rows.extend(batch)
        start += len(batch)
        if start >= data.get("total", 0):
            return rows
        if not batch:
            break
    raise ValueError("Jira comment pagination exceeded limit")


async def jira_action(
    name,
    inputs,
    project_connections,
    cancelled,
    workspace,
    transport=None,
    *,
    config=None,
):
    try:
        connection = project_connections[inputs["connection"]]
    except KeyError as error:
        raise ValueError(
            f"Jira connection '{inputs['connection']}' is not defined "
            "in project configuration"
        ) from error
    url = connection["url"]
    if not url.startswith("https://"):
        raise ValueError("Jira connections require HTTPS")
    from aorch.runner.executor import project_secret

    token = project_secret(connection["token_env"])
    if not token:
        raise ValueError("Missing Jira credential")
    auth = (
        httpx.BasicAuth(connection["email"], token) if connection.get("email") else None
    )
    headers = {} if auth else {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        base_url=url, auth=auth, headers=headers, timeout=30, transport=transport
    ) as client:
        if name == "jira.discover":
            jql = inputs.get("jql")
            if not jql:
                literal = lambda value: json.dumps(str(value))
                jql = f"assignee = {literal(inputs['assignee'])} AND status = {literal(inputs['status'])}"
                if inputs.get("project"):
                    jql += f" AND project = {literal(inputs['project'])}"
            items, cursor = [], None
            for _ in range(100):
                if cancelled.is_set():
                    return StepResult(status="cancelled", summary="Discovery stopped")
                body = {
                    "jql": jql,
                    "fields": [
                        "summary",
                        "description",
                        "attachment",
                        "status",
                        "assignee",
                    ],
                    "maxResults": 100,
                }
                if cursor:
                    body["nextPageToken"] = cursor
                response = await client.post("/rest/api/3/search/jql", json=body)
                response.raise_for_status()
                data = response.json()
                for issue in data.get("issues", []):
                    rows = await comments(client, issue["key"])
                    items.append(
                        {
                            "id": issue["key"],
                            "summary": issue["fields"].get("summary"),
                            "description": issue["fields"].get("description"),
                            "comments": rows,
                            "attachments": issue["fields"].get("attachment", []),
                            "provider": issue,
                        }
                    )
                next_cursor = data.get("nextPageToken")
                if not next_cursor or data.get("isLast", False):
                    return StepResult(
                        status="succeeded",
                        summary=f"Discovered {len(items)} work items",
                        outputs={"work_items": items},
                    )
                if next_cursor == cursor:
                    raise ValueError("Jira pagination did not advance")
                cursor = next_cursor
            raise ValueError("Jira search exceeded pagination limit")
        key = inputs["issue"]
        if name == "jira.transition":
            target = inputs["status"]
            issue_path = f"/rest/api/3/issue/{quote(key, safe='')}"

            async def current_status():
                response = await client.get(issue_path, params={"fields": "status"})
                response.raise_for_status()
                return response.json()["fields"]["status"]["name"]

            current = await current_status()
            if current.casefold() == target.casefold():
                return StepResult(
                    status="succeeded",
                    summary=f"Jira issue is already {target}",
                    outputs={"issue": key, "status": current},
                )
            response = await client.get(f"{issue_path}/transitions")
            response.raise_for_status()
            transitions = response.json().get("transitions", [])
            transition = next(
                (
                    item
                    for item in transitions
                    if item.get("to", {}).get("name", "").casefold()
                    == target.casefold()
                ),
                None,
            )
            if transition is None:
                raise ValueError(
                    f"Jira issue {key} is currently '{current}'. Jira does not offer "
                    f"a transition to '{target}' for this issue. Check its workflow "
                    "and the Jira account's transition permissions."
                )
            post_error = None
            try:
                response = await client.post(
                    f"{issue_path}/transitions",
                    json={"transition": {"id": transition["id"]}},
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                post_error = error
            try:
                current = await current_status()
            except (httpx.HTTPError, KeyError, TypeError) as error:
                if post_error is None:
                    return StepResult(
                        status="needs_attention",
                        error="JiraTransitionVerificationFailed",
                        summary=(
                            f"Jira accepted the request to move {key} to '{target}', "
                            "but Runestra could not verify the resulting status. "
                            f"Check {key} in Jira before retrying this step. "
                            f"Verification error: {jira_error_label(error)}."
                        ),
                    )
                return StepResult(
                    status="needs_attention",
                    error="JiraTransitionOutcomeUnknown",
                    summary=(
                        f"Runestra could not confirm whether Jira moved {key} to "
                        f"'{target}', and it could not read the current Jira status. "
                        f"Check {key} in Jira, then retry this step only if it is "
                        f"not already '{target}'. Request error: "
                        f"{jira_error_label(post_error)}. Status check error: "
                        f"{jira_error_label(error)}."
                    ),
                )
            if post_error is not None and current.casefold() == target.casefold():
                return StepResult(
                    status="succeeded",
                    summary=(
                        f"Jira issue {key} is now '{target}'. Jira did not return a "
                        "confirmation for the transition request, but a status check "
                        "confirmed the change."
                    ),
                    outputs={"issue": key, "status": current},
                )
            if post_error is not None and current.casefold() != target.casefold():
                return StepResult(
                    status="needs_attention",
                    error="JiraTransitionOutcomeUnknown",
                    summary=(
                        f"Jira did not confirm moving {key} to '{target}'. The latest "
                        f"status check says '{current}'. Check Jira activity, then "
                        f"retry this step if {key} is still '{current}'. Request "
                        f"error: {jira_error_label(post_error)}."
                    ),
                )
            if current.casefold() != target.casefold():
                raise ValueError(
                    f"Jira issue {key} is still '{current}' after a successful "
                    f"request to move it to '{target}'. Check the Jira workflow "
                    "and transition permissions."
                )
            return StepResult(
                status="succeeded",
                summary=f"Jira issue transitioned to {target}",
                outputs={"issue": key, "status": current},
            )
        if name == "jira.report_failure":
            marker = f"[aorch:failure:{inputs['run_id']}]"
            root = config.workspace_root if config else workspace
            ledger = EffectLedger(root / "effects.sqlite")
            effect = ledger.get(marker)
            rows = await comments(client, key)
            found = next((row for row in rows if marker in plain_text(row["body"])), None)
            if found:
                provider_id = found["id"]
                ledger.intent(marker)
                ledger.done(marker, {"provider_id": provider_id})
            elif effect:
                if effect["status"] != "done":
                    return StepResult(status="needs_attention", summary="Failure report outcome uncertain; reconcile before retry")
                provider_id = effect["result"]["provider_id"]
            else:
                from aorch.runner.executor import (
                    project_secret,
                    project_secret_names_context,
                )

                report = {
                    key: inputs[key]
                    for key in ("run_id", "failed_step", "status", "summary", "error", "attempt_id")
                }
                report["diagnostics"] = inputs.get("diagnostics", {})
                report["artifacts"] = inputs.get("artifacts", [])
                report["failures"] = inputs.get("failures", [])
                text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
                for secret_name in project_secret_names_context.get():
                    secret = project_secret(secret_name)
                    if secret:
                        text = text.replace(secret, "[REDACTED]")
                if len(text) > 24000:
                    text = text[:24000] + "\n[diagnostics truncated]"
                text += "\n" + marker
                if cancelled.is_set():
                    return StepResult(status="cancelled", summary="Failure report stopped before publication")
                ledger.intent(marker)
                try:
                    response = await client.post(
                        f"/rest/api/3/issue/{quote(key, safe='')}/comment",
                        json={"body": adf("Runestra workflow failure\n" + text)},
                    )
                    response.raise_for_status()
                    provider_id = response.json()["id"]
                except (httpx.HTTPError, KeyError, ValueError):
                    return StepResult(status="needs_attention", summary="Failure report outcome uncertain; reconcile before retry")
                ledger.done(marker, {"provider_id": provider_id})
            return StepResult(
                status="succeeded",
                summary="Workflow failure recorded on Jira",
                outputs={"external_effects": [{"key": marker, "kind": "jira.failure_report", "provider_id": provider_id}]},
            )
        ambiguities = inputs["ambiguities"]
        identifiers = sorted(item["id"] for item in ambiguities)
        identity = hashlib.sha256(
            json.dumps([url, key, identifiers]).encode()
        ).hexdigest()
        marker = f"[aorch:clarification:{identity}]"
        root = config.workspace_root if config else workspace
        ledger = EffectLedger(root / "effects.sqlite")
        effect = ledger.get(marker)
        rows = await comments(client, key)
        found = next((row for row in rows if marker in plain_text(row["body"])), None)
        if found:
            provider_id = found["id"]
            ledger.intent(marker)
            ledger.done(marker, {"provider_id": provider_id})
        elif effect:
            if effect["status"] != "done":
                return StepResult(
                    status="needs_attention",
                    summary="Clarification outcome uncertain; provider marker not found",
                )
            provider_id = effect["result"]["provider_id"]
        else:
            if cancelled.is_set():
                return StepResult(
                    status="cancelled", summary="Comment stopped before publication"
                )
            text = (
                "\n".join(f"{item['id']}: {item['question']}" for item in ambiguities)
                + "\n"
                + marker
            )
            ledger.intent(marker)
            try:
                response = await client.post(
                    f"/rest/api/3/issue/{quote(key, safe='')}/comment",
                    json={"body": adf(text)},
                )
                response.raise_for_status()
                provider_id = response.json()["id"]
            except (httpx.HTTPError, KeyError, ValueError):
                return StepResult(
                    status="needs_attention",
                    summary="Jira comment outcome uncertain; reconcile before retry",
                )
            ledger.done(marker, {"provider_id": provider_id})
        return StepResult(
            status="succeeded",
            summary="Clarification comment recorded",
            outputs={
                "external_effects": [
                    {"key": marker, "kind": "jira.comment", "provider_id": provider_id}
                ]
            },
        )


def jira_error_label(error):
    if isinstance(error, httpx.HTTPStatusError):
        return f"Jira returned HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "request timed out"
    if isinstance(error, httpx.ConnectError):
        return "could not connect to Jira"
    return type(error).__name__
