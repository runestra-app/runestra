import asyncio
from types import SimpleNamespace

import httpx
import pytest

from aorch.extensions.github import github_action


def run(action, inputs, handler, monkeypatch, *, credential="test-token"):
    monkeypatch.setenv("GITHUB_TEST_TOKEN", credential)
    config = SimpleNamespace(connections={"github": {"token_env": "GITHUB_TEST_TOKEN"}})
    transport = httpx.MockTransport(handler)
    return asyncio.run(github_action(action, inputs, config, asyncio.Event(), transport=transport))


def issue(number, *, pull_request=False):
    value = {
        "number": number,
        "title": f"Issue {number}",
        "body": "Description",
        "html_url": f"https://github.com/acme/widget/issues/{number}",
        "labels": [{"name": "bug"}],
        "state": "open",
        "updated_at": "2026-10-11T00:00:00Z",
    }
    if pull_request:
        value["pull_request"] = {"url": "https://api.github.com/repos/acme/widget/pulls/2"}
    return value


def test_github_discovery_maps_issues_and_excludes_pull_requests(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.params["labels"] == "bug,urgent"
        assert request.url.params["assignee"] == "me"
        return httpx.Response(200, json=[issue(1), issue(2, pull_request=True)])

    result = run(
        "github.discover",
        {"connection": "github", "repository": "acme/widget", "labels": ["bug", "urgent"], "assignee": "me"},
        handler,
        monkeypatch,
    )
    assert [item["id"] for item in result.outputs["work_items"]] == ["1"]
    assert len(requests) == 1


def test_github_discovery_stops_at_bounded_page_limit(monkeypatch):
    pages = []

    def handler(request):
        pages.append(int(request.url.params["page"]))
        return httpx.Response(200, json=[issue(number) for number in range(100)])

    with pytest.raises(ValueError, match="500-item limit"):
        run(
            "github.discover",
            {"connection": "github", "repository": "acme/widget"},
            handler,
            monkeypatch,
        )
    assert pages == [1, 2, 3, 4, 5]


def test_github_get_rejects_pull_requests_and_bad_credentials(monkeypatch):
    with pytest.raises(ValueError, match="not accepted as issue work items"):
        run(
            "github.get",
            {"connection": "github", "repository": "acme/widget", "issue": 2},
            lambda request: httpx.Response(200, json=issue(2, pull_request=True)),
            monkeypatch,
        )
    with pytest.raises(ValueError, match="rejected the configured credential"):
        run(
            "github.get",
            {"connection": "github", "repository": "acme/widget", "issue": 2},
            lambda request: httpx.Response(401, json={"message": "Bad credentials"}),
            monkeypatch,
        )


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (403, {"X-RateLimit-Remaining": "0", "Retry-After": "60"}, "rate limit exceeded.*60 seconds"),
        (429, {}, "rate limit exceeded"),
    ],
)
def test_github_rate_limits_have_actionable_errors(monkeypatch, status, headers, message):
    with pytest.raises(ValueError, match=message):
        run(
            "github.discover",
            {"connection": "github", "repository": "acme/widget"},
            lambda request: httpx.Response(status, headers=headers),
            monkeypatch,
        )
