import json
import pytest
import websockets
from httpx import AsyncClient
from fastapi import status
from chatagent_ws.main import app, APP_WS_HOST, APP_WS_PORT, APP_WS_API_KEY, APP_CONNECTION_MAX_REQUESTS_PER_MINUTE

# Mark the module as requiring asyncio
pytestmark = pytest.mark.asyncio


# Fixture to create an async HTTP client
@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url=f"http://{APP_WS_HOST}:{APP_WS_PORT}") as ac:
        yield ac


# Test the /api/get_session_token endpoint
async def test_get_session_token(client: AsyncClient):
    headers = {"x-api-key": APP_WS_API_KEY}
    response = await client.post("/api/get_session_token", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "token" in data
    assert "expires_in" in data
    assert "session_id" in data
    assert isinstance(data["token"], str)
    assert isinstance(data["expires_in"], int)
    assert isinstance(data["session_id"], str)


# Test invalid API key
async def test_get_session_token_invalid_api_key(client: AsyncClient):
    headers = {"x-api-key": "invalid-key"}
    response = await client.post("/api/get_session_token", headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Invalid API Key"


# Test WebSocket connection and messaging
async def test_websocket_chat(client: AsyncClient):
    # Step 1: Get a session token
    headers = {"x-api-key": APP_WS_API_KEY}
    response = await client.post("/api/get_session_token", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["token"]

    # Step 2: Connect to WebSocket
    ws_url = f"ws://{APP_WS_HOST}:{APP_WS_PORT}/ws?token={token}"
    async with websockets.connect(ws_url) as websocket:
        # Step 3: Receive initial chat history (should be empty)
        initial_message = await websocket.recv()
        initial_data = json.loads(initial_message)
        assert "chat_history" in initial_data
        assert isinstance(initial_data["chat_history"], list)
        assert len(initial_data["chat_history"]) == 0

        # Step 4: Send a message and receive a response
        test_message = {"message": "Hello, bot!"}
        await websocket.send(json.dumps(test_message))
        response = await websocket.recv()
        response_data = json.loads(response)

        assert "user_message" in response_data
        assert "bot_response" in response_data
        assert "bot_suggestions" in response_data
        assert response_data["user_message"] == "Hello, bot!"
        assert isinstance(response_data["bot_response"], str)


# Test WebSocket with invalid token
async def test_websocket_invalid_token():
    ws_url = f"ws://{APP_WS_HOST}:{APP_WS_PORT}/ws?token=invalid-token"
    async with websockets.connect(ws_url) as websocket:
        error_message = await websocket.recv()
        error_data = json.loads(error_message)
        assert "error" in error_data
        assert error_data["error"] in ["Invalid token", "Token IP mismatch", "Token expired"]


# Test WebSocket rate limiting
async def test_websocket_rate_limit(client: AsyncClient):
    # Get a session token
    headers = {"x-api-key": APP_WS_API_KEY}
    response = await client.post("/api/get_session_token", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["token"]

    # Connect to WebSocket
    ws_url = f"ws://{APP_WS_HOST}:{APP_WS_PORT}/ws?token={token}"
    async with websockets.connect(ws_url) as websocket:
        # Receive initial chat history
        await websocket.recv()

        # Send messages exceeding the rate limit
        for _ in range(APP_CONNECTION_MAX_REQUESTS_PER_MINUTE + 1):
            await websocket.send(json.dumps({"message": "Test rate limit"}))
            response = await websocket.recv()
            response_data = json.loads(response)

            if "error" in response_data:
                assert response_data["error"] == "Rate limit exceeded"
                break
        else:
            pytest.fail("Rate limit not enforced")


# Test cleanup after WebSocket disconnect
async def test_websocket_disconnect(client: AsyncClient):
    # Get a session token
    headers = {"x-api-key": APP_WS_API_KEY}
    response = await client.post("/api/get_session_token", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["token"]

    # Connect and immediately disconnect
    ws_url = f"ws://{APP_WS_HOST}:{APP_WS_PORT}/ws?token={token}"
    async with websockets.connect(ws_url) as websocket:
        await websocket.recv()  # Initial history
        # Disconnect happens when context manager exits

    # Reconnect to verify history is cleared
    async with websockets.connect(ws_url) as websocket:
        initial_message = await websocket.recv()
        initial_data = json.loads(initial_message)
        assert "chat_history" in initial_data
        assert len(initial_data["chat_history"]) == 0


if __name__ == "__main__":
    pytest.main(["-v"])