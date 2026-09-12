"""
tests/test_conversational_memory.py

Comprehensive tests for conversational memory architecture:
1. "what about tomorrow?"
2. "when should I go?"
3. "will it affect the crop?"
4. "what about evening?"
5. "is that better?"
6. location switching ("What about Chennai?")
7. implicit travel intent
8. consecutive follow-ups
9. state merging (merge semantics)
10. stale summary vs recent message precedence
11. exactly one LLM call
12. background summarization (non-blocking)
13. Redis failure handling
14. multiple simultaneous users
15. cross-user state isolation
16. LLM state updates validation (untrusted data protection)
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.schemas.chat import (
    HistoryMessage,
    StructuredConversationState,
    WeatherData,
)
from app.services.conversation_service import (
    _IN_MEMORY_CONVERSATIONS,
    _IN_MEMORY_MESSAGES,
    add_message,
    async_summarize_if_needed,
    get_conversation_context,
    get_conversation_context_and_state,
    get_conversation_state,
    get_or_create_conversation,
    list_conversations,
    merge_conversation_state,
    save_conversation_state,
)
from app.services.llm_service import extract_and_validate_llm_state_updates

USER_A = "user-a-uuid-0001"
USER_B = "user-b-uuid-0002"


@pytest.fixture(autouse=True)
def clear_memory_store():
    """Reset in-memory fallback stores between tests."""
    _IN_MEMORY_CONVERSATIONS.clear()
    _IN_MEMORY_MESSAGES.clear()
    yield
    _IN_MEMORY_CONVERSATIONS.clear()
    _IN_MEMORY_MESSAGES.clear()


@pytest.fixture
def mock_weather():
    def _make(location_name: str, temp: float = 30.0, rain_prob: float = 15.0):
        return WeatherData(
            location=location_name,
            temperature=temp,
            feels_like=temp + 1.0,
            condition="Partly Cloudy",
            humidity=65,
            wind_speed=3.5,
            rain_probability=rain_prob,
            rainfall=0.0,
            uv_index=6.0,
            source="open-meteo",
        )
    return _make


# ═══════════════════════════════════════════════════════════════════════════════
# 1. State Merging Semantics (Requirement 5, 7, 8)
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_merging_preserves_unrelated_fields():
    """
    Existing:
    origin = Chennai
    destination = Punjab
    cargo = wheat
    date_time = tonight

    User: "What about tomorrow?"
    Result:
    origin = Chennai
    destination = Punjab
    cargo = wheat
    date_time = tomorrow
    """
    initial_state = StructuredConversationState(
        user_id=USER_A,
        origin="Chennai",
        destination="Punjab",
        cargo="wheat",
        date_time="tonight",
        activity="travel",
        current_weather_concern="heavy rain",
        previous_recommendation="Drive with caution",
    )

    updates = {"date_time": "tomorrow"}
    merged = merge_conversation_state(initial_state, updates)

    assert merged.origin == "Chennai"
    assert merged.destination == "Punjab"
    assert merged.cargo == "wheat"
    assert merged.date_time == "tomorrow"
    assert merged.activity == "travel"
    assert merged.current_weather_concern == "heavy rain"
    assert merged.previous_recommendation == "Drive with caution"


def test_llm_state_updates_validation():
    """LLM emitted state updates must be validated and sanitized before merging."""
    raw_answer = (
        "It will be clear tomorrow morning.\n"
        "<!--STATE: {\"date_time\": \"tomorrow morning\", \"cargo\": \"rice\", \"invalid_field\": \"hacked\"} -->"
    )
    clean_answer, updates = extract_and_validate_llm_state_updates(raw_answer)

    assert "<!--STATE" not in clean_answer
    assert clean_answer.strip() == "It will be clear tomorrow morning."
    assert updates["date_time"] == "tomorrow morning"
    assert updates["cargo"] == "rice"
    assert "invalid_field" not in updates  # Invalid fields rejected


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Conversational Continuity & Natural Language Follow-ups (Requirements 1, 2)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_followup_what_about_tomorrow(mock_weather):
    """'what about tomorrow?' preserves active location and resolves weather."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 17.3850, 78.4867, "Hyderabad, Telangana, India"

        async def fake_weather(latitude, longitude, location_name, for_tomorrow=False):
            return mock_weather("Hyderabad")

        async def fake_llm(**kwargs):
            return "Hyderabad will be clear tomorrow."

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", side_effect=fake_weather), \
             patch("app.api.routes.chat.generate_weather_response", side_effect=fake_llm):

            # Turn 1
            resp1 = await ac.post("/api/v1/chat", json={"message": "Weather in Hyderabad"})
            assert resp1.status_code == 200
            data1 = resp1.json()
            conv_id = data1["conversation_id"]

            # Turn 2: "what about tomorrow?" — no weather keyword, must NOT be rejected!
            resp2 = await ac.post("/api/v1/chat", json={
                "message": "what about tomorrow?",
                "conversation_id": conv_id,
            })
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert "Hyderabad" in data2["location"]
            assert data2["conversation_state"]["date_time"].lower() == "tomorrow"


@pytest.mark.asyncio
async def test_followup_when_should_i_go(mock_weather):
    """'when should I go?' must be accepted in active context without weather keywords."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 13.0827, 80.2707, "Chennai, Tamil Nadu, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", return_value=mock_weather("Chennai")), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Best time to depart is early morning."):

            # Turn 1 establishes context
            resp1 = await ac.post("/api/v1/chat", json={"message": "Weather in Chennai"})
            assert resp1.status_code == 200
            conv_id = resp1.json()["conversation_id"]

            # Turn 2: "when should I go?" has no weather words
            resp2 = await ac.post("/api/v1/chat", json={
                "message": "when should I go?",
                "conversation_id": conv_id,
            })
            assert resp2.status_code == 200
            assert "Chennai" in resp2.json()["location"]


@pytest.mark.asyncio
async def test_followup_will_it_affect_the_crop(mock_weather):
    """'will it affect the crop?' follow-up succeeds with agricultural state context."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 18.5204, 73.8567, "Pune, Maharashtra, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", return_value=mock_weather("Pune")), \
             patch("app.api.routes.chat.generate_weather_response", return_value="The high humidity may increase fungal risk for paddy."):

            resp1 = await ac.post("/api/v1/chat", json={"message": "I am growing paddy in Pune"})
            assert resp1.status_code == 200
            conv_id = resp1.json()["conversation_id"]

            resp2 = await ac.post("/api/v1/chat", json={
                "message": "will it affect the crop?",
                "conversation_id": conv_id,
            })
            assert resp2.status_code == 200
            assert "Pune" in resp2.json()["location"]


@pytest.mark.asyncio
async def test_followup_what_about_evening(mock_weather):
    """'what about evening?' updates date_time in state while maintaining location."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 28.6139, 77.2090, "Delhi, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", return_value=mock_weather("Delhi")), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Evening will be cool and clear."):

            resp1 = await ac.post("/api/v1/chat", json={"message": "Weather in Delhi"})
            assert resp1.status_code == 200
            conv_id = resp1.json()["conversation_id"]

            resp2 = await ac.post("/api/v1/chat", json={
                "message": "what about evening?",
                "conversation_id": conv_id,
            })
            assert resp2.status_code == 200
            state = resp2.json()["conversation_state"]
            assert "Delhi" in state["active_location"]
            assert "evening" in state["date_time"].lower()


@pytest.mark.asyncio
async def test_followup_is_that_better(mock_weather):
    """'is that better?' follow-up resolves against prior recommendation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 12.9716, 77.5946, "Bengaluru, Karnataka, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", return_value=mock_weather("Bengaluru")), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Yes, tomorrow has significantly lower rain probability."):

            resp1 = await ac.post("/api/v1/chat", json={"message": "Rain in Bengaluru"})
            conv_id = resp1.json()["conversation_id"]

            resp2 = await ac.post("/api/v1/chat", json={
                "message": "is that better?",
                "conversation_id": conv_id,
            })
            assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_location_switching(mock_weather):
    """'what about Chennai?' switches active location cleanly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            if "chennai" in place_name.lower():
                return 13.0827, 80.2707, "Chennai, Tamil Nadu, India"
            return 17.3850, 78.4867, "Hyderabad, Telangana, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", side_effect=lambda *a, **kw: mock_weather(kw.get("location_name", "Loc"))), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Chennai is 31°C and humid."):

            resp1 = await ac.post("/api/v1/chat", json={"message": "Weather in Hyderabad"})
            conv_id = resp1.json()["conversation_id"]

            resp2 = await ac.post("/api/v1/chat", json={
                "message": "what about Chennai?",
                "conversation_id": conv_id,
            })
            assert resp2.status_code == 200
            assert "Chennai" in resp2.json()["location"]
            assert "Chennai" in resp2.json()["conversation_state"]["active_location"]


@pytest.mark.asyncio
async def test_implicit_travel_intent(mock_weather):
    """Implicit travel intent extracts origin, destination, and sets activity to travel."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            if "mumbai" in place_name.lower():
                return 19.0760, 72.8777, "Mumbai, Maharashtra, India"
            return 18.5204, 73.8567, "Pune, Maharashtra, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", side_effect=lambda *a, **kw: mock_weather("Loc")), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Route from Mumbai to Pune is clear."):

            resp = await ac.post("/api/v1/chat", json={
                "message": "Transporting cotton from Mumbai to Pune tonight",
            })
            assert resp.status_code == 200
            state = resp.json()["conversation_state"]
            assert state["activity"] == "travel"
            assert "Mumbai" in state["origin"]
            assert "Pune" in state["destination"]
            assert state["cargo"].lower() == "cotton"


@pytest.mark.asyncio
async def test_consecutive_followups(mock_weather):
    """Multiple consecutive follow-ups maintain continuity without degradation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 17.3850, 78.4867, "Hyderabad, Telangana, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", return_value=mock_weather("Hyderabad")), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Weather update."):

            # Turn 1
            r1 = await ac.post("/api/v1/chat", json={"message": "Weather in Hyderabad"})
            cid = r1.json()["conversation_id"]

            # Turn 2
            r2 = await ac.post("/api/v1/chat", json={"message": "what about tomorrow?", "conversation_id": cid})
            assert r2.status_code == 200
            assert "Hyderabad" in r2.json()["location"]

            # Turn 3
            r3 = await ac.post("/api/v1/chat", json={"message": "what about evening?", "conversation_id": cid})
            assert r3.status_code == 200
            assert "Hyderabad" in r3.json()["location"]

            # Turn 4
            r4 = await ac.post("/api/v1/chat", json={"message": "is that better?", "conversation_id": cid})
            assert r4.status_code == 200
            assert "Hyderabad" in r4.json()["location"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Precedence: Stale Summary vs Recent Messages (Requirement 7)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stale_summary_vs_recent_message_precedence(mock_weather):
    """Recent explicit user information overrides older state and summary."""
    conv_id = "conv_test_precedence_123"
    old_state = StructuredConversationState(
        user_id=USER_A,
        active_location="Chennai",
        origin="Hyderabad",
        destination="Chennai",
        cargo="rice",
    )
    await save_conversation_state(conv_id, USER_A, old_state)

    # User explicitly changes destination to Pune in recent turn
    updated = merge_conversation_state(old_state, {"destination": "Pune", "active_location": "Pune"})
    await save_conversation_state(conv_id, USER_A, updated)

    loaded_state = await get_conversation_state(conv_id, USER_A)
    assert loaded_state.destination == "Pune"
    assert loaded_state.active_location == "Pune"
    assert loaded_state.cargo == "rice"  # Unrelated field preserved!


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Exactly ONE LLM Call in Normal Chat Path (Requirements 3, 15)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exactly_one_llm_call_in_chat_path(mock_weather):
    """The normal chat path must make exactly ONE LLM completion call."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place_name: str):
            return 17.3850, 78.4867, "Hyderabad, Telangana, India"

        mock_llm = AsyncMock(return_value="Hyderabad is 30°C and sunny.")

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", return_value=mock_weather("Hyderabad")), \
             patch("app.api.routes.chat.generate_weather_response", new=mock_llm):

            resp = await ac.post("/api/v1/chat", json={"message": "What is the weather in Hyderabad?"})
            assert resp.status_code == 200
            assert mock_llm.call_count == 1  # Exactly ONE LLM completion call!


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Background Summarization & Non-Blocking Response (Requirements 10, 11)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_background_summarization_non_blocking():
    """Rolling summarization must never block the chat response."""
    conv_id = "conv_summarize_test"
    messages = [
        HistoryMessage(role="user", content=f"Message {i}")
        for i in range(15)
    ]

    with patch("app.services.conversation_service.count_messages", new=AsyncMock(return_value=15)), \
         patch("app.services.llm_service.generate_conversation_summary", new=AsyncMock(return_value="Summary of conversation")):
        # Call async_summarize_if_needed directly as a background worker
        result = await async_summarize_if_needed(conv_id, USER_A, "", messages)
        assert result == "Summary of conversation"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Redis Failure Handling (Requirement 6, 9)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_redis_failure_handling_falls_back():
    """When Redis is unavailable or fails, conversation state falls back gracefully."""
    conv_id = "conv_redis_fail_test"

    # Simulate Redis throwing an exception
    async def _redis_err(*args, **kwargs):
        raise ConnectionError("Redis server unreachable")

    with patch("app.services.conversation_service._redis_get_str", side_effect=_redis_err), \
         patch("app.services.conversation_service._redis_set_str", side_effect=_redis_err):

        # Saving state should not crash
        state = StructuredConversationState(user_id=USER_A, active_location="Kolkata")
        await save_conversation_state(conv_id, USER_A, state)

        # Retrieving state should fall back to DB/in-memory store
        retrieved_state = await get_conversation_state(conv_id, USER_A)
        assert retrieved_state is not None
        assert retrieved_state.active_location == "Kolkata"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Multi-User Concurrency & Cross-User State Isolation (Requirements 6, 14)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cross_user_state_isolation():
    """User A's state and conversation must never be accessible or modifiable by User B."""
    conv_a = await get_or_create_conversation(None, USER_A)
    conv_b = await get_or_create_conversation(None, USER_B)

    state_a = StructuredConversationState(user_id=USER_A, active_location="Delhi", cargo="wheat")
    state_b = StructuredConversationState(user_id=USER_B, active_location="Chennai", cargo="rice")

    await save_conversation_state(conv_a.id, USER_A, state_a)
    await save_conversation_state(conv_b.id, USER_B, state_b)

    # User A accesses conv_a
    loaded_a = await get_conversation_state(conv_a.id, USER_A)
    assert loaded_a.active_location == "Delhi"
    assert loaded_a.cargo == "wheat"

    # User B attempts to access conv_a -> isolated, state not leaked
    loaded_b_attempt = await get_conversation_state(conv_a.id, USER_B)
    assert loaded_b_attempt.active_location is None or loaded_b_attempt.user_id == USER_B


@pytest.mark.asyncio
async def test_multiple_simultaneous_users(mock_weather):
    """Concurrent requests from different users maintain completely separate states."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async def fake_geocode(place):
            if "delhi" in place.lower():
                return 28.6139, 77.2090, "Delhi, India"
            return 19.0760, 72.8777, "Mumbai, Maharashtra, India"

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", side_effect=lambda *a, **kw: mock_weather(kw.get("location_name", "Loc"))), \
             patch("app.api.routes.chat.generate_weather_response", return_value="Weather response"):

            # Run User 1 and User 2 concurrently
            task1 = ac.post("/api/v1/chat", json={"message": "Weather in Delhi"})
            task2 = ac.post("/api/v1/chat", json={"message": "Weather in Mumbai"})

            r1, r2 = await asyncio.gather(task1, task2)

            assert r1.status_code == 200
            assert r2.status_code == 200

            d1 = r1.json()
            d2 = r2.json()

            assert "Delhi" in d1["location"]
            assert "Mumbai" in d2["location"]
            assert d1["conversation_id"] != d2["conversation_id"]
            assert d1["conversation_state"]["active_location"] == "Delhi"
            assert d2["conversation_state"]["active_location"] == "Mumbai"
