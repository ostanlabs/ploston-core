"""Specification tests for ploston_core.native_tools.firecrawl.

Only the external boundary (httpx) is mocked. Endpoint URL construction,
payload shaping, auth header wiring, result processing, URL filtering, and
error handling all run for real.
"""

from typing import Any

import httpx
import pytest

from ploston_core.native_tools import firecrawl

# ---------------------------------------------------------------------------
# httpx mocking helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Any = None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self) -> Any:
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(self.status_code),
            )


def _patch_client(monkeypatch, *, post=None, get=None):
    """Patch firecrawl.httpx.AsyncClient. `post`/`get` are handler callables."""
    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["init_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return post(url, headers, json)

        async def get(self, url, **kwargs):
            captured["url"] = url
            return get(url)

    monkeypatch.setattr(firecrawl.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


# ---------------------------------------------------------------------------
# search_web_firecrawl
# ---------------------------------------------------------------------------


async def test_search_endpoint_payload_and_auth(monkeypatch):
    captured = _patch_client(
        monkeypatch,
        post=lambda u, h, j: _FakeResponse(200, {"success": True, "data": [{"x": 1}, {"y": 2}]}),
    )

    result = await firecrawl.search_web_firecrawl(
        query="cats", base_url="http://fc:3002", api_key="secret", limit=5
    )

    assert captured["url"] == "http://fc:3002/v1/search"
    # Self-hosted v1 only supports query + limit.
    assert captured["json"] == {"query": "cats", "limit": 5}
    assert captured["headers"]["Authorization"] == "Bearer secret"

    assert result["success"] is True
    assert result["data"] == [{"x": 1}, {"y": 2}]
    assert result["query"] == "cats"
    assert result["result_count"] == 2
    assert "2 results" in result["summary"]


async def test_search_no_api_key_omits_auth_header(monkeypatch):
    captured = _patch_client(
        monkeypatch, post=lambda u, h, j: _FakeResponse(200, {"success": True, "data": []})
    )

    await firecrawl.search_web_firecrawl(query="q", base_url="http://fc:3002")
    assert "Authorization" not in captured["headers"]


async def test_search_api_failure_raises(monkeypatch):
    _patch_client(
        monkeypatch,
        post=lambda u, h, j: _FakeResponse(200, {"success": False, "error": "rate limited"}),
    )

    with pytest.raises(Exception, match="rate limited"):
        await firecrawl.search_web_firecrawl(query="q", base_url="http://fc:3002")


async def test_search_http_error_raises(monkeypatch):
    _patch_client(monkeypatch, post=lambda u, h, j: _FakeResponse(500, {}))

    with pytest.raises(httpx.HTTPStatusError):
        await firecrawl.search_web_firecrawl(query="q", base_url="http://fc:3002")


# ---------------------------------------------------------------------------
# map_website_firecrawl
# ---------------------------------------------------------------------------


async def test_map_endpoint_and_payload(monkeypatch):
    captured = _patch_client(
        monkeypatch,
        post=lambda u, h, j: _FakeResponse(
            200, {"success": True, "links": ["http://a.com/1", "http://a.com/2"]}
        ),
    )

    result = await firecrawl.map_website_firecrawl(
        url="http://a.com", base_url="http://fc:3002", api_key="k", limit=50
    )

    assert captured["url"] == "http://fc:3002/v1/map"
    assert captured["json"]["url"] == "http://a.com"
    assert captured["json"]["limit"] == 50
    assert captured["headers"]["Authorization"] == "Bearer k"

    assert result["success"] is True
    assert result["urls"] == ["http://a.com/1", "http://a.com/2"]
    assert result["total_urls"] == 2
    assert result["original_link_count"] == 2
    assert result["excluded_count"] == 0


async def test_map_filters_excluded_domains(monkeypatch):
    _patch_client(
        monkeypatch,
        post=lambda u, h, j: _FakeResponse(
            200,
            {
                "success": True,
                "links": ["http://keep.com/a", "http://drop.com/b", "http://keep.com/c"],
            },
        ),
    )

    result = await firecrawl.map_website_firecrawl(
        url="http://x.com",
        base_url="http://fc:3002",
        exclude_domains=["drop.com"],
    )

    assert "http://drop.com/b" not in result["urls"]
    assert result["filtered_link_count"] == 2
    assert result["excluded_count"] == 1
    assert "excluded 1" in result["summary"]


async def test_map_optional_payload_fields(monkeypatch):
    captured = _patch_client(
        monkeypatch, post=lambda u, h, j: _FakeResponse(200, {"success": True, "links": []})
    )

    await firecrawl.map_website_firecrawl(
        url="http://x.com",
        base_url="http://fc:3002",
        exclude_tags=["nav", "footer"],
        custom_headers={"X-Test": "1"},
    )

    assert captured["json"]["excludeTags"] == ["nav", "footer"]
    assert captured["json"]["headers"] == {"X-Test": "1"}


async def test_map_failure_raises(monkeypatch):
    _patch_client(
        monkeypatch, post=lambda u, h, j: _FakeResponse(200, {"success": False, "error": "boom"})
    )
    with pytest.raises(Exception, match="boom"):
        await firecrawl.map_website_firecrawl(url="http://x.com", base_url="http://fc:3002")


# ---------------------------------------------------------------------------
# extract_data_firecrawl
# ---------------------------------------------------------------------------


async def test_extract_endpoint_payload_and_result(monkeypatch):
    captured = _patch_client(
        monkeypatch,
        post=lambda u, h, j: _FakeResponse(200, {"success": True, "data": {"title": "Hello"}}),
    )

    urls = ["http://a.com", "http://b.com"]
    result = await firecrawl.extract_data_firecrawl(
        urls=urls,
        base_url="http://fc:3002",
        api_key="k",
        schema={"type": "object"},
        prompt="get titles",
    )

    assert captured["url"] == "http://fc:3002/v1/extract"
    assert captured["json"]["urls"] == urls
    assert captured["json"]["schema"] == {"type": "object"}
    assert captured["json"]["prompt"] == "get titles"
    assert captured["headers"]["Authorization"] == "Bearer k"

    assert result["success"] is True
    assert result["data"] == {"title": "Hello"}
    assert result["total_urls"] == 2


async def test_extract_omits_unset_optional_fields(monkeypatch):
    captured = _patch_client(
        monkeypatch, post=lambda u, h, j: _FakeResponse(200, {"success": True, "data": None})
    )

    await firecrawl.extract_data_firecrawl(urls=["http://a.com"], base_url="http://fc:3002")

    assert "schema" not in captured["json"]
    assert "prompt" not in captured["json"]
    assert "headers" not in captured["json"]


async def test_extract_failure_raises(monkeypatch):
    _patch_client(
        monkeypatch, post=lambda u, h, j: _FakeResponse(200, {"success": False, "error": "nope"})
    )
    with pytest.raises(Exception, match="nope"):
        await firecrawl.extract_data_firecrawl(urls=["http://a.com"], base_url="http://fc:3002")


# ---------------------------------------------------------------------------
# check_health_firecrawl
# ---------------------------------------------------------------------------


async def test_health_endpoint_and_healthy(monkeypatch):
    captured = _patch_client(monkeypatch, get=lambda u: _FakeResponse(200))

    result = await firecrawl.check_health_firecrawl(base_url="http://fc:3002")
    assert captured["url"] == "http://fc:3002/"
    assert result["success"] is True
    assert result["status"] == "healthy"
    assert result["response_code"] == 200


async def test_health_4xx_still_healthy(monkeypatch):
    # Contract: <500 means the service is up (healthy).
    _patch_client(monkeypatch, get=lambda u: _FakeResponse(404))
    result = await firecrawl.check_health_firecrawl(base_url="http://fc:3002")
    assert result["success"] is True
    assert result["status"] == "healthy"


async def test_health_5xx_unhealthy(monkeypatch):
    _patch_client(monkeypatch, get=lambda u: _FakeResponse(503))
    result = await firecrawl.check_health_firecrawl(base_url="http://fc:3002")
    assert result["success"] is False
    assert result["status"] == "unhealthy"
    assert result["response_code"] == 503
