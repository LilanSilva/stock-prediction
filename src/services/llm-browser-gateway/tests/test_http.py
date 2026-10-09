import httpx
import pytest
from conftest import FakeAdapter
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_contract import BASE

HEADERS = {"Authorization": "Bearer " + "a" * 32}


async def test_http_errors_and_payload(running: FastAPI, adapter: FakeAdapter) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=running), base_url="http://gateway"
    ) as client:
        assert (await client.post("/v1/chat/completions", json=BASE)).status_code == 401
        for path, body, code in [
            ("/v1/chat/completions?x=1", BASE, 400),
            ("/v1/chat/completions", BASE | {"stream": True}, 400),
            ("/v1/responses", BASE, 404),
        ]:
            response = await client.post(path, json=body, headers=HEADERS)
            assert response.status_code == code and "error" in response.json()
        bad = await client.post(
            "/v1/chat/completions",
            content="{broken",
            headers=HEADERS | {"Content-Type": "application/json"},
        )
        assert bad.status_code == 400
        oversized = await client.post(
            "/v1/chat/completions",
            content=" " * 262145,
            headers=HEADERS | {"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        bad = await client.post(
            "/v1/chat/completions",
            content='{"model":"browser-auto","messages":[],"n":NaN}',
            headers=HEADERS | {"Content-Type": "application/json"},
        )
        assert bad.status_code == 400
        response = await client.post("/v1/chat/completions", json=BASE, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "hello"
        assert adapter.seen == [BASE]
        assert (await client.get("/status", headers=HEADERS)).json()["priority"] == [
            "chatgpt",
            "claude",
        ]


def test_real_openai_sdk_parses_response(app: FastAPI) -> None:
    openai = pytest.importorskip("openai")
    with TestClient(app) as transport:
        client = openai.OpenAI(
            api_key="a" * 32, base_url="http://testserver/v1", http_client=transport, max_retries=0
        )
        response = client.chat.completions.create(**BASE)
        assert response.choices[0].message.content == "hello"
        assert response.usage is None and response.model == "chatgpt-web"


async def test_real_async_openai_sdk(running: FastAPI) -> None:
    openai = pytest.importorskip("openai")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=running)) as transport:
        async with openai.AsyncOpenAI(
            api_key="a" * 32, base_url="http://testserver/v1", http_client=transport, max_retries=0
        ) as client:
            response = await client.chat.completions.create(**BASE)
            assert response.choices[0].message.content == "hello"


def test_extension_origin_auth_and_disconnect(app: FastAPI) -> None:
    with TestClient(app) as client:
        with client.websocket_connect(
            "/bridge", headers={"origin": "chrome-extension://" + "a" * 32}
        ) as ws:
            ws.send_json(
                {
                    "type": "authenticate",
                    "profileId": "default",
                    "token": "b" * 32,
                    "extensionVersion": "0.1.1",
                    "protocolVersion": 2,
                }
            )
            assert ws.receive_json()["type"] == "authenticated"
            status = client.get("/status", headers=HEADERS).json()
            assert status["extension_version"] == "0.1.1"
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
        assert app.state.bridge.socket is None
        assert app.state.bridge.extension_version is None
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/bridge", headers={"origin": "https://untrusted.example"}
            ):
                pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/bridge", headers={"origin": "chrome-extension://" + "a" * 32}
            ) as ws:
                ws.send_json({"type": "authenticate", "profileId": "default", "token": "wrong"})
                assert ws.receive_json() == {"type": "connection_error", "code": "invalid_pairing"}
                ws.receive_json()


def test_older_extension_cannot_receive_concurrent_dispatch(app: FastAPI) -> None:
    with TestClient(app) as client:
        with client.websocket_connect(
            "/bridge", headers={"origin": "chrome-extension://" + "a" * 32}
        ) as ws:
            ws.send_json({"type": "authenticate", "profileId": "default", "token": "b" * 32})
            assert ws.receive_json() == {"type": "connection_error", "code": "update_required"}
            assert app.state.bridge.socket is None
