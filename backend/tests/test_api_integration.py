from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def _wait_for_run(client: TestClient, run_id: str, timeout_seconds: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        if response.status_code == 200:
            payload = response.json()
            status = payload["data"]["run"]["status"]
            if status in {"succeeded", "failed", "canceled"}:
                return payload
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in time")


def test_startup_creates_system_files(client: TestClient) -> None:
    ctx = client.app.state.ctx
    assert ctx.paths.root.exists()
    assert ctx.paths.settings.exists()
    assert ctx.paths.installed_sources.exists()
    assert ctx.paths.lockfile.exists()


def test_install_enable_run_and_items_lookup(client: TestClient, demo_source_path: Path) -> None:
    install_response = client.post("/sources/install", json={"path": str(demo_source_path)})
    assert install_response.status_code == 201

    installed = client.get("/sources/installed")
    sources = installed.json()["data"]["sources"]
    assert len(sources) == 1

    source_uid = sources[0]["uid"]
    enable_response = client.post(f"/sources/{source_uid}/enable")
    assert enable_response.status_code == 200

    run_response = client.post("/digest/run")
    assert run_response.status_code == 202
    run_id = run_response.json()["data"]["run_id"]

    run_payload = _wait_for_run(client, run_id)
    assert run_payload["data"]["run"]["status"] == "succeeded"

    current_digest = client.get("/digest/current")
    assert current_digest.status_code == 200

    digest_data = current_digest.json()["data"]["output_json"]
    bullets = digest_data["interests"][next(iter(digest_data["interests"]))]
    refs = bullets[0]["source_refs"]
    assert refs

    refs_query = ",".join(refs)
    items_response = client.get(f"/items?refs={refs_query}")
    assert items_response.status_code == 200
    items = items_response.json()["data"]["items"]
    assert len(items) >= 1
    assert items[0]["item_ref"] in refs


def test_concurrent_digest_rejected(
    client: TestClient,
    demo_source_path: Path,
    monkeypatch: Any,
) -> None:
    install_response = client.post("/sources/install", json={"path": str(demo_source_path)})
    assert install_response.status_code == 201
    source_uid = install_response.json()["data"]["uid"]
    assert client.post(f"/sources/{source_uid}/enable").status_code == 200

    ctx = client.app.state.ctx
    original_execute = ctx.source_executor.execute

    async def delayed_execute(source: Any, cursor: str | None) -> Any:
        await asyncio.sleep(0.75)
        return await original_execute(source, cursor)

    monkeypatch.setattr(ctx.source_executor, "execute", delayed_execute)

    first_run = client.post("/digest/run")
    assert first_run.status_code == 202

    second_run = client.post("/digest/run")
    assert second_run.status_code == 409
    assert second_run.json()["error_code"] == "DIGEST_ALREADY_RUNNING"


def test_fallback_digest_path(client: TestClient, demo_source_path: Path, monkeypatch: Any) -> None:
    install_response = client.post("/sources/install", json={"path": str(demo_source_path)})
    source_uid = install_response.json()["data"]["uid"]
    assert client.post(f"/sources/{source_uid}/enable").status_code == 200

    ctx = client.app.state.ctx

    def bad_summarize(items: list[Any], interests_text: str) -> str:
        _ = items
        _ = interests_text
        return "not-json"

    def bad_repair(invalid_output: str, errors: list[str], items: list[Any], interests_text: str) -> str:
        _ = invalid_output
        _ = errors
        _ = items
        _ = interests_text
        return "still-not-json"

    monkeypatch.setattr(ctx.digest_service._summarizer, "summarize", bad_summarize)
    monkeypatch.setattr(ctx.digest_service._summarizer, "repair", bad_repair)

    run_response = client.post("/digest/run")
    run_id = run_response.json()["data"]["run_id"]

    run_payload = _wait_for_run(client, run_id)
    run_record = run_payload["data"]["run"]
    assert run_record["status"] == "succeeded"
    assert run_record["error_code"] == "LLM_FALLBACK_USED"

    digest = client.get("/digest/current").json()["data"]["output_json"]
    assert "fallback" in digest["extraneous"].lower()
