"""Opt-in eight-request integration check through the running gateway and real browsers."""

import asyncio
import json
import os
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIError, APIStatusError, AsyncOpenAI

from llm_browser_gateway.config import Settings

pytestmark = [
    pytest.mark.live_browser,
    pytest.mark.skipif(
        os.getenv("BROWSER_GATEWAY_LIVE_BATCH_TEST") != "1",
        reason="Opt in to eight synthetic requests using the paired websites",
    ),
]


async def test_eight_requests_share_four_slots_and_report_provider_counts() -> None:
    settings = Settings()
    base = f"http://127.0.0.1:{settings.port}"
    providers = ("chatgpt", "claude")
    count = 8
    headers = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=10) as monitor:

        async def status() -> dict[str, Any]:
            response = await monitor.get("/status")
            response.raise_for_status()
            state: dict[str, Any] = response.json()
            return state

        initial = await status()
        assert initial["extension_connected"], "Connect the Chrome extension first"
        assert initial["priority"] == list(providers), "Use normal ChatGPT-then-Claude routing"
        assert initial["tabs_per_provider"] == 2, "This check expects two tabs per provider"
        assert not initial["requests"] and not initial["active"], "Wait for other work to finish"
        now = datetime.now(UTC)
        assert all(
            item["next_check_at"] is not None
            and datetime.fromisoformat(item["next_check_at"]) <= now
            for item in initial["availability"]
        ), "Wait for active cooldowns or resolve unknown availability before this capacity check"

        peak_requests = peak_occupied = peak_waiting = peak_submitted = 0
        peak_by_provider: Counter[str] = Counter()
        attempts_by_slot: dict[str, set[str]] = defaultdict(set)
        providers_by_request: dict[str, set[str]] = defaultdict(set)
        observer_errors: list[str] = []
        capacity_violations: list[str] = []
        stop, launch = asyncio.Event(), asyncio.Event()
        queued_observed = False
        run_id = uuid.uuid4().hex[:12]
        started_at = datetime.now(UTC).isoformat()

        async def observe() -> None:
            nonlocal peak_requests, peak_occupied, peak_waiting, peak_submitted, queued_observed
            while not stop.is_set():
                try:
                    state = await status()
                except httpx.HTTPError as exc:
                    observer_errors.append(type(exc).__name__)
                    return
                active = state["active"]
                keys = [(item["provider"], item["slot"]) for item in active]
                occupied = Counter(provider for provider, _ in keys)
                if len(keys) != len(set(keys)) or len(keys) > 4:
                    capacity_violations.append("Duplicate or excessive occupied slots")
                if any(provider not in providers or slot not in (0, 1) for provider, slot in keys):
                    capacity_violations.append("Unexpected provider or slot")
                for provider in providers:
                    peak_by_provider[provider] = max(peak_by_provider[provider], occupied[provider])
                    if occupied[provider] > 2:
                        capacity_violations.append(f"Too many occupied slots for {provider}")
                for item in active:
                    key = f"{item['provider']}:{item['slot']}"
                    attempts_by_slot[key].add(item["attempt_id"])
                    providers_by_request[item["request_id"]].add(item["provider"])
                peak_requests = max(peak_requests, state["requests"])
                peak_occupied = max(peak_occupied, len(active))
                waiting = max(0, state["requests"] - len(active))
                if len(active) == 4:
                    peak_waiting = max(peak_waiting, waiting)
                peak_submitted = max(
                    peak_submitted, sum(item["state"] == "submitted" for item in active)
                )
                if not queued_observed and len(active) == 4 and waiting >= 4:
                    queued_observed = True
                    print("Observed four occupied slots and four waiting requests", flush=True)
                try:
                    await asyncio.wait_for(stop.wait(), 0.1)
                except TimeoutError:
                    pass

        async with AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=base + "/v1",
            timeout=settings.deadline_seconds + 30,
            max_retries=0,
        ) as client:

            async def request(index: int) -> dict[str, Any]:
                expected = f"batch-{run_id}-{index}"
                await launch.wait()
                began = time.monotonic()
                row: dict[str, Any] = {"request": index + 1}
                try:
                    response = await client.chat.completions.create(
                        model=settings.model_alias,
                        messages=[{"role": "user", "content": f"Reply with exactly: {expected}"}],
                    )
                    provider = response.model.removesuffix("-web")
                    valid = (
                        response.model in {f"{name}-web" for name in providers}
                        and bool(response.choices)
                        and response.choices[0].message.content == expected
                    )
                    row.update(provider=provider, success=valid, response_id=response.id)
                    if not valid:
                        row["error"] = "Unexpected provider or crossed/incorrect response"
                except APIError as exc:
                    row.update(success=False, error=type(exc).__name__, code=exc.code)
                    if isinstance(exc, APIStatusError):
                        row["http_status"] = exc.status_code
                row["elapsed_seconds"] = round(time.monotonic() - began, 3)
                print(json.dumps(row), flush=True)
                return row

            observer = asyncio.create_task(observe())
            tasks = [asyncio.create_task(request(index)) for index in range(count)]
            started = time.monotonic()
            launch.set()
            try:
                # API failures are recorded per request; other in-flight requests still finish.
                rows = await asyncio.gather(*tasks)
            finally:
                stop.set()
                await observer
            elapsed = time.monotonic() - started

        final = await status()
        served = Counter(row["provider"] for row in rows if row["success"])
        report = {
            "run_id": run_id,
            "started_at_utc": started_at,
            "extension_version": initial["extension_version"],
            "priority": initial["priority"],
            "initial_availability": initial["availability"],
            "request_count": count,
            "batch_seconds": round(elapsed, 3),
            "successful_responses_by_provider": {name: served[name] for name in providers},
            "failed_requests": sum(not row["success"] for row in rows),
            "peak_requests": peak_requests,
            "peak_occupied_slots": peak_occupied,
            "peak_submitted": peak_submitted,
            "peak_waiting_with_full_pool": peak_waiting,
            "peak_occupied_by_provider": dict(peak_by_provider),
            "observed_attempts_by_provider": {
                name: sum(
                    len(ids) for key, ids in attempts_by_slot.items() if key.startswith(name + ":")
                )
                for name in providers
            },
            "observed_attempts_by_slot": {key: len(ids) for key, ids in attempts_by_slot.items()},
            "observed_providers_by_request": {
                key: sorted(names) for key, names in providers_by_request.items()
            },
            "capacity_violations": capacity_violations,
            "observer_errors": observer_errors,
            "requests": rows,
            "final_status": final,
        }
        report_path = (
            Path(__file__).resolve().parents[1] / ".runtime" / "batch-integration-latest.json"
        )
        report_path.parent.mkdir(exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        print(f"Report: {report_path}", flush=True)

        assert not observer_errors and not capacity_violations, report
        assert all(len(names) == 1 for names in providers_by_request.values()), (
            "One API request was dispatched to multiple providers"
        )
        assert all(row["success"] for row in rows), "Failed requests; see saved batch report"
        assert set(served) == set(providers), "Both providers must handle real gateway requests"
        assert len({row["response_id"] for row in rows}) == count, "Duplicate completion IDs"
        assert peak_requests == count and queued_observed, "Four-running/four-waiting not observed"
        assert peak_occupied == 4 and all(peak_by_provider[name] == 2 for name in providers)
        assert len(attempts_by_slot) == 4 and any(len(ids) > 1 for ids in attempts_by_slot.values())
        assert not final["active"] and not final["requests"], "Gateway work remains unresolved"
