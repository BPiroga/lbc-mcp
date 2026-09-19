"""Outils MCP, avec un faux Leboncoin a la place du reseau."""

import lbc
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from lbc_mcp import server
from lbc_mcp.leboncoin import LeboncoinError

from .conftest import make_raw_ad

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeContext:
    def __init__(self):
        self.progress = []

    async def report_progress(self, progress, total=None, message=None):
        self.progress.append((progress, total))


def fake_page(ids, max_pages=10):
    raw = {"total": 1234, "total_private": 1000, "total_pro": 234, "max_pages": max_pages}
    search = lbc.Search._build(raw | {"ads": [make_raw_ad(ad_id=i) for i in ids]}, client=None)
    return search


async def test_tools_are_listed_with_lbc_choices():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert set(tools) == {"search_ads", "get_ad", "get_seller"}
    schema = tools["search_ads"].input_schema
    assert "ELECTRONIQUE_CONSOLES" in str(schema["properties"]["category"])
    assert "ctx" not in schema["properties"]


async def test_search_walks_pages_dedups_and_stops_at_last_page(monkeypatch):
    pages = {1: fake_page([1, 2], max_pages=2), 2: fake_page([2, 3], max_pages=2)}
    calls = []

    def search_page(prepared, limit, page):
        calls.append(page)
        return pages[page]

    monkeypatch.setattr(server.gateway, "search_page", search_page)
    ctx = FakeContext()
    result = await server.search_ads(ctx, query="switch", departments=["69"], limit=2, pages=5)

    assert calls == [1, 2]
    assert [ad["id"] for ad in result["ads"]] == [1, 2, 3]
    assert result["next_page"] is None
    assert result["total"] == 1234
    assert ctx.progress == [(1, 5), (2, 5)]


async def test_search_keeps_what_it_has_when_blocked_midway(monkeypatch):
    def search_page(prepared, limit, page):
        if page == 2:
            raise LeboncoinError("bloque")
        return fake_page([page * 10, page * 10 + 1])

    monkeypatch.setattr(server.gateway, "search_page", search_page)
    result = await server.search_ads(FakeContext(), query="switch", limit=2, pages=3)

    assert result["returned"] == 2
    assert result["next_page"] == 2
    assert "bloque" in result["notes"][0]


async def test_search_fails_cleanly_when_first_page_is_blocked(monkeypatch):
    def search_page(prepared, limit, page):
        raise LeboncoinError("bloque")

    monkeypatch.setattr(server.gateway, "search_page", search_page)
    with pytest.raises(ToolError, match="bloque"):
        await server.search_ads(FakeContext(), query="switch")


async def test_large_inline_request_is_redirected_to_export():
    with pytest.raises(ToolError, match="export_format"):
        await server.search_ads(FakeContext(), query="switch", limit=100, pages=4)


async def test_export_returns_file_and_sample(monkeypatch, tmp_path):
    monkeypatch.setenv("LBC_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        server.gateway, "search_page", lambda prepared, limit, page: fake_page(range(page * 100, page * 100 + 100))
    )
    result = await server.search_ads(
        FakeContext(), query="switch", limit=100, pages=4, export_format="csv"
    )
    assert result["returned"] == 400
    assert len(result["sample"]) == 5
    assert "ads" not in result
    assert result["export_file"].startswith(str(tmp_path))
