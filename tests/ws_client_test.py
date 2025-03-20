import asyncio
import json
import requests
import websockets
from typing import Optional

# Configuration (adjust these to match your server settings)
API_HOST = "localhost"
API_PORT = 8001  # Match your APP_WS_PORT
API_KEY = "SFffSD5@#J"  # Replace with your APP_WS_API_KEY

BASE_URL = f"http://{API_HOST}:{API_PORT}"
WS_URL_TEMPLATE = f"ws://{API_HOST}:{API_PORT}/ws?token={{token}}"


async def get_session_token() -> Optional[str]:
    """Fetch a session token from the server."""
    url = f"{BASE_URL}/api/get_session_token"
    headers = {"x-api-key": API_KEY}

    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        print(f"Got session token: {data['token'][:10]}... (expires in {data['expires_in']}s)")
        return data["token"]
    except requests.RequestException as e:
        print(f"Failed to get session token: {e}")
        return None


# ... (other imports and code unchanged) ...

async def chat_with_server(token: str):
    ws_url = WS_URL_TEMPLATE.format(token=token)
    async with websockets.connect(ws_url) as websocket:
        initial_message = await websocket.recv()
        initial_data = json.loads(initial_message)
        print("Initial chat history:", initial_data.get("chat_history", []))

        try:
            while True:
                message = input("You: ").strip()
                if message.lower() in ["quit", "exit"]:
                    print("Closing connection...")
                    await websocket.close(code=1000, reason="User requested disconnect")
                    return

                await websocket.send(json.dumps({"message": message}))
                response = await websocket.recv()
                response_data = json.loads(response)

                if "error" in response_data:
                    print(f"Error: {response_data['error']}")
                elif response_data.get("type") == "ping":
                    print("(Received ping from server)")
                else:
                    print(f"Bot: {response_data['bot_response']}")
                    if response_data["bot_suggestions"]:
                        print("Suggestions:")
                        for suggestion in response_data["bot_suggestions"].split("\n"):
                            print(f"  {suggestion}")
        except websockets.ConnectionClosed as e:
            print(f"Connection closed: {e}")


async def main():
    """Main entry point for the client."""
    token = await get_session_token()
    if token:
        await chat_with_server(token)
    else:
        print("Cannot proceed without a valid session token.")


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())