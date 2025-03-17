import pytest
import json
from unittest.mock import Mock, patch
from flask import Flask
from flask_socketio import SocketIO
from main import app, socketio, ChatHistory, AIResponse, generate_session_token, validate_token, \
    get_bot_response, validate_api_key, session_tokens  # Replace 'main' with actual filename
from src.chatagent_ws.AppConfig import APP_WS_API_KEY


# Test fixtures
@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_socketio():
    with patch('main.socketio') as mock:
        yield mock


@pytest.fixture
def chat_history():
    return ChatHistory()


# Unit Tests
def test_generate_session_token():
    session_id = "test-session"
    client_ip = "127.0.0.1"
    token = generate_session_token(session_id, client_ip)

    assert isinstance(token, str)
    assert len(token) > 0
    assert token in session_tokens
    assert session_tokens[token]["session_id"] == session_id
    assert session_tokens[token]["ip"] == client_ip


def test_validate_token_valid():
    session_id = "test-session"
    client_ip = "127.0.0.1"
    token = generate_session_token(session_id, client_ip)

    valid, result = validate_token(token, client_ip)
    assert valid is True
    assert result == session_id


def test_validate_token_invalid_ip():
    session_id = "test-session"
    client_ip = "127.0.0.1"
    token = generate_session_token(session_id, client_ip)

    valid, result = validate_token(token, "192.168.1.1")
    assert valid is False
    assert result == "Token IP mismatch"


def test_chat_history(chat_history):
    session_id = "test-session"

    # Test empty history
    assert chat_history.get_history(session_id) == []

    # Test append
    chat_history.append(session_id, {"text": "test message"})
    history = chat_history.get_history(session_id)
    assert len(history) == 1
    assert history[0] == {"text": "test message"}

    # Test clear
    chat_history.clear_history(session_id)
    assert chat_history.get_history(session_id) == []


def test_validate_api_key():
    # Assuming APP_WS_API_KEY is set in configuration
    with patch('main.APP_WS_API_KEY', 'test-api-key'):
        assert validate_api_key('test-api-key') is True
        assert validate_api_key('wrong-key') is False


# Integration Tests
def test_get_session_token(client):
    headers = {'x-api-key': APP_WS_API_KEY}  # Replace with valid API key

    with patch('main.validate_api_key', return_value=True):
        response = client.post('/api/get_session_token', headers=headers)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "token" in data
        assert "session_id" in data
        assert "expires_in" in data


def test_get_session_token_invalid_api_key(client):
    headers = {'x-api-key': 'wrong-key'}

    with patch('main.validate_api_key', return_value=False):
        response = client.post('/api/get_session_token', headers=headers)
        assert response.status_code == 401
        assert json.loads(response.data) == {"error": "Invalid API key"}


# WebSocket Tests
def test_socketio_connect(mock_socketio, client):
    token = generate_session_token("test-session", "127.0.0.1")

    with patch('main.validate_token', return_value=(True, "test-session")):
        with patch('main.check_rate_limits', return_value=(True, "")):
            test_client = socketio.test_client(app, query_string=f'token={token}')
            assert test_client.is_connected()
            received = test_client.get_received()
            assert len(received) > 0
            assert received[0]['name'] == 'message'


def test_socketio_message(mock_socketio, client):
    token = generate_session_token("test-session", "127.0.0.1")
    mock_response = AIResponse(message="Hello back", suggestions=["suggestion1"])

    with patch('main.validate_token', return_value=(True, "test-session")):
        with patch('main.check_rate_limits', return_value=(True, "")):
            with patch('main.get_bot_response', return_value=mock_response):
                test_client = socketio.test_client(app, query_string=f'token={token}')
                test_client.emit('message', {'message': 'Hello'})
                received = test_client.get_received()
                print(f"test_socketio_message: {received}")
                assert any(event['name'] == 'message' and
                          'bot_response' in event['args'] and  # Check if key exists
                          event['args']['bot_response'] == "Hello back"  # Access dict key
                          for event in received)

# Mock external API call
def test_get_bot_response():
    with patch('requests.post') as mock_post:
        mock_response = Mock()
        mock_response.text = json.dumps({"message": "Test response", "suggestions": ["s1"]})
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        response = get_bot_response("test message", "test-session")
        assert isinstance(response, AIResponse)
        assert response.message == "Test response"
        assert response.suggestions == ["s1"]


if __name__ == '__main__':
    pytest.main()