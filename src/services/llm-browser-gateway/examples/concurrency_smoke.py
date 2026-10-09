"""Compare sequential and concurrent synthetic SDK requests against one real provider."""

import argparse
import asyncio
import time
import uuid

import httpx
from openai import AsyncOpenAI

from llm_browser_gateway.config import Settings


async def check(provider: str, count: int) -> None:
    settings = Settings()
    base = f"http://127.0.0.1:{settings.port}"
    headers = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}
    async with httpx.AsyncClient(base_url=base, headers=headers) as status_client:
        response = await status_client.get("/status")
        response.raise_for_status()
        state = response.json()
        assert state["priority"] == [provider], "Configure single-provider priority for this check"
        assert state["extension_connected"] and state["tabs_per_provider"] >= 2
        assert not state["active"], "Wait for existing gateway work to finish first"

        async with AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=base + "/v1",
            timeout=settings.deadline_seconds + 30,
            max_retries=0,
        ) as client:

            async def request(index: int) -> None:
                expected = f"sample-{index}-{uuid.uuid4().hex[:8]}"
                reply = await client.chat.completions.create(
                    model=settings.model_alias,
                    messages=[{"role": "user", "content": f"Reply with exactly: {expected}"}],
                )
                assert reply.model == f"{provider}-web"
                assert reply.choices[0].message.content == expected, "Responses crossed requests"

            started = time.monotonic()
            for index in range(count):
                await request(index)
            sequential = time.monotonic() - started
            print(
                f"{provider}: {count} sequential requests passed in {sequential:.1f}s", flush=True
            )

            stop = asyncio.Event()
            observed_slots: set[int] = set()
            maximum_active = 0

            async def observe() -> None:
                nonlocal maximum_active
                while not stop.is_set():
                    sample = await status_client.get("/status")
                    sample.raise_for_status()
                    active = [
                        item
                        for item in sample.json()["active"]
                        if item["provider"] == provider and item["state"] == "submitted"
                    ]
                    maximum_active = max(maximum_active, len(active))
                    observed_slots.update(item["slot"] for item in active)
                    await asyncio.sleep(0.1)

            observer = asyncio.create_task(observe())
            started = time.monotonic()
            try:
                await asyncio.gather(*(request(index) for index in range(count)))
            finally:
                stop.set()
                await observer
            parallel = time.monotonic() - started
            assert maximum_active >= 2 and len(observed_slots) >= 2, (
                "No overlapping tab submissions"
            )
            print(
                f"{provider}: {count} concurrent requests passed in {parallel:.1f}s; "
                f"peak submissions={maximum_active}; slots={sorted(observed_slots)}; "
                f"measured batch speedup={sequential / parallel:.2f}x",
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["chatgpt", "claude"])
    parser.add_argument("--requests", type=int, default=2, choices=range(2, 7))
    args = parser.parse_args()
    asyncio.run(check(args.provider, args.requests))
