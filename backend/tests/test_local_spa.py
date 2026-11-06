"""Static SPA isolation on the real qualified HTTP listener; no dev database/provider."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as application
from app.core.config import build_sqlite_url
from app.core.execution_environment import prepare_local_execution_server
from app.core.local_spa import install_local_spa
from test_workflow_execution_api import HTTPHarness, HTTPSettings
from test_workflow_execution_api import http_env as http_env
from workflow_read_fixtures import db as db


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def build(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><div id="root">LOCAL_SPA</div></html>', encoding="utf-8"
    )
    (root / "assets" / "index-12345678.js").write_text("const built = true;", encoding="utf-8")
    (root / "assets" / "private.env").write_text("PRIVATE_BUILD_SECRET", encoding="utf-8")
    (root / "assets" / "index.js.map").write_text("PRIVATE_SOURCE_MAP", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return root


@pytest.mark.anyio
async def test_same_origin_spa_navigation_assets_and_workflow(
    http_env: HTTPHarness, build: Path
) -> None:
    env = http_env
    install_local_spa(env.app, build, transport=env.transport)
    for path in ["/", "/history", f"/prompt-requests/{env.db.source.id}"]:
        page = await env.client.get(path)
        assert page.status_code == 200 and "LOCAL_SPA" in page.text
        assert page.headers["cache-control"] == "no-store"
    asset = await env.client.get("/assets/index-12345678.js")
    assert asset.status_code == 200 and asset.headers["cache-control"] == "no-cache"
    assert asset.headers["x-content-type-options"] == "nosniff"
    assert (await env.client.get("/favicon.svg")).status_code == 200
    workflow = await env.workflow()
    response = await env.start(workflow)
    assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
    env.provider.release.set()
    await env.supervisor.wait_idle()
    progress = await env.client.get(f"/api/v1/runs/{workflow}")
    assert progress.json()["status"] == "COMPLETED"
    assert progress.headers["cache-control"] == "no-store"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/missing",
        "/api/missing",
        "/openapi.json",
        "/health",
        "/docs/missing",
        "/redoc/missing",
        "/.env",
        "/assets/private.env",
        "/assets/index.js.map",
        "/assets/missing.js",
        "/assets/../index.html",
        "/unknown",
        "/history/nested",
    ],
)
async def test_no_fallback_for_reserved_unknown_or_private_paths(
    http_env: HTTPHarness, build: Path, path: str
) -> None:
    install_local_spa(http_env.app, build, transport=http_env.transport)
    response = await http_env.client.get(path)
    assert response.status_code == 404
    assert "LOCAL_SPA" not in response.text and "PRIVATE_" not in response.text
    assert http_env.provider.calls == 0


@pytest.mark.anyio
async def test_api_and_openapi_keep_real_responses(http_env: HTTPHarness, build: Path) -> None:
    env = http_env
    install_local_spa(env.app, build, transport=env.transport)
    schema = (await env.client.get("/api/v1/openapi.json")).json()
    assert "/history" not in schema["paths"]
    assert "/api/v1/runs/{id}/execution" in schema["paths"]
    assert (await env.client.get("/api/v1/health")).status_code == 200
    assert "LOCAL_SPA" not in (await env.client.get("/docs")).text
    assert (
        (await env.client.get("/api/v1/prompt-requests"))
        .headers["content-type"]
        .startswith("application/json")
    )
    assert (await env.client.post("/history", json={})).status_code == 405


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.test"},
        {"Origin": "http://evil.test"},
        {"Forwarded": "for=127.0.0.1"},
        {"X-Forwarded-For": "127.0.0.1"},
    ],
)
async def test_spa_reuses_transport_policy(
    http_env: HTTPHarness, build: Path, headers: dict[str, str]
) -> None:
    install_local_spa(http_env.app, build, transport=http_env.transport)
    for path in ["/", "/assets/index-12345678.js"]:
        assert (await http_env.client.get(path, headers=headers)).status_code == 403


@pytest.mark.anyio
async def test_lost_listener_authority_blocks_spa(http_env: HTTPHarness, build: Path) -> None:
    install_local_spa(http_env.app, build, transport=http_env.transport)
    http_env.transport.server.config.workers = 2
    try:
        for path in ["/", "/assets/index-12345678.js"]:
            assert (await http_env.client.get(path)).status_code == 503
    finally:
        http_env.transport.server.config.workers = 1


def test_default_app_has_no_spa_and_preparation_alone_cannot_serve(build: Path) -> None:
    app = application.create_app(HTTPSettings(database_url=build_sqlite_url(build / "absent.db")))
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
    with (
        prepare_local_execution_server(app, port=0) as prepared,
        pytest.raises(ValueError, match="explicit local execution"),
    ):
        install_local_spa(app, build, transport=prepared)
    app = application.create_app(
        HTTPSettings(
            database_url=build_sqlite_url(build / "absent.db"),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
        )
    )
    with prepare_local_execution_server(app, port=0) as prepared:
        install_local_spa(app, build, transport=prepared)
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
            assert client.get("/").status_code == 503
            assert not (build / "absent.db").exists()
