import dataclasses

import httpx
import pytest
import respx

from mysql_documentation_mcp.core.fetcher import Fetcher, FetchError, OfflineError

URL = "https://dev.mysql.com/doc/refman/8.4/en/select.html"


@pytest.fixture
async def fetcher(settings):
    f = Fetcher(settings)
    yield f
    await f.aclose()


@respx.mock
async def test_fetch_sends_user_agent(fetcher, settings):
    route = respx.get(URL).respond(200, text="<html></html>")
    result = await fetcher.fetch(URL)
    assert result.status_code == 200
    assert result.final_url == URL
    assert route.calls.last.request.headers["user-agent"] == settings.user_agent


@respx.mock
async def test_retries_transient_errors(fetcher):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(200, text="ok"),
        ]
    )
    result = await fetcher.fetch(URL)
    assert result.text == "ok"
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_max_retries(fetcher, settings):
    route = respx.get(URL).respond(503)
    with pytest.raises(FetchError, match="HTTP 503"):
        await fetcher.fetch(URL)
    assert route.call_count == settings.max_retries + 1


@respx.mock
async def test_404(fetcher):
    respx.get(URL).respond(404)
    with pytest.raises(FetchError, match="Page not found"):
        await fetcher.fetch(URL)


@respx.mock
async def test_403_mentions_user_agent_setting(fetcher):
    respx.get(URL).respond(403)
    with pytest.raises(FetchError, match="MYSQL_DOC_USER_AGENT"):
        await fetcher.fetch(URL)


@respx.mock
async def test_follows_allowed_redirect(fetcher):
    target = "https://dev.mysql.com/doc/refman/9.7/en/select.html"
    respx.get(URL).respond(301, headers={"Location": "/doc/refman/9.7/en/select.html"})
    respx.get(target).respond(200, text="ok")
    result = await fetcher.fetch(URL)
    assert result.final_url == target


@respx.mock
async def test_refuses_redirect_outside_allowlist(fetcher):
    respx.get(URL).respond(302, headers={"Location": "https://evil.example/x.html"})
    evil = respx.get("https://evil.example/x.html").respond(200)
    with pytest.raises(FetchError, match="Refusing to follow redirect"):
        await fetcher.fetch(URL)
    assert not evil.called


async def test_refuses_url_outside_allowlist(fetcher):
    with pytest.raises(FetchError, match="outside the allowlist"):
        await fetcher.fetch("https://example.com/")


async def test_offline_mode(settings):
    fetcher = Fetcher(dataclasses.replace(settings, offline=True))
    with pytest.raises(OfflineError):
        await fetcher.fetch(URL)


@respx.mock
async def test_conditional_request(fetcher):
    zip_url = "https://downloads.mysql.com/docs/mysql-8.4.info.zip"
    route = respx.get(zip_url).respond(304)
    result = await fetcher.fetch(zip_url, last_modified="Fri, 25 Sep 2026 16:25:27 GMT")
    assert result.not_modified
    assert route.calls.last.request.headers["if-modified-since"] == "Fri, 25 Sep 2026 16:25:27 GMT"


@respx.mock
async def test_refuses_redirect_to_other_downloads(fetcher):
    respx.get(URL).respond(
        302, headers={"Location": "https://downloads.mysql.com/docs/mysql-8.4-relnotes-en.pdf"}
    )
    with pytest.raises(FetchError, match="Refusing to follow redirect"):
        await fetcher.fetch(URL)


@respx.mock
async def test_refuses_oversized_response(settings):
    fetcher = Fetcher(dataclasses.replace(settings, max_response_bytes=1000))
    try:
        respx.get(URL).respond(200, content=b"x" * 5000)  # declared Content-Length
        with pytest.raises(FetchError, match="exceeds 1000"):
            await fetcher.fetch(URL)

        async def chunks():
            for _ in range(10):
                yield b"x" * 500

        # No Content-Length: the body is cut off while streaming.
        respx.get(URL).mock(return_value=httpx.Response(200, content=chunks()))
        with pytest.raises(FetchError, match="exceeds 1000 bytes"):
            await fetcher.fetch(URL)
    finally:
        await fetcher.aclose()
