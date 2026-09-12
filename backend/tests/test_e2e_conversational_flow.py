"""
Integration test for the exact user conversational flow:
1. User: "What's the weather in Hyderabad?" -> Resolves Hyderabad
2. User: "What about tomorrow?" -> Resolves Hyderabad + tomorrow
3. User: "What about Chennai?" -> Switches location to Chennai
4. User: "Will it be safe to travel there?" -> Resolves "there" to Chennai
"""

import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.schemas.chat import WeatherData


@pytest.fixture
def mock_weather_data():
    def _make(location_name: str, temp: float = 30.0, rain_prob: float = 10.0):
        return WeatherData(
            location=location_name,
            temperature=temp,
            feels_like=temp + 1.0,
            condition="Sunny",
            humidity=60,
            wind_speed=3.0,
            rain_probability=rain_prob,
            rainfall=0.0,
            uv_index=5.0,
            source="open-meteo",
        )
    return _make


@pytest.mark.asyncio
async def test_complete_conversational_memory_flow(mock_weather_data):
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Mock geocoding and weather so test is deterministic and offline-capable
        async def fake_geocode(place_name: str):
            clean = place_name.strip()
            if "hyderabad" in clean.lower():
                return 17.3850, 78.4867, "Hyderabad, Telangana, India"
            elif "chennai" in clean.lower():
                return 13.0827, 80.2707, "Chennai, Tamil Nadu, India"
            return 20.0, 78.0, clean

        async def fake_weather(latitude, longitude, location_name, for_tomorrow=False):
            return mock_weather_data(location_name or "TestLocation")

        async def fake_llm(**kwargs):
            q = kwargs.get("user_question", "")
            w = kwargs.get("weather_data")
            loc = w.location if w else "unknown"
            return f"Answer for {loc}: question was '{q}'."

        with patch("app.api.routes.chat.geocode_location", side_effect=fake_geocode), \
             patch("app.api.routes.chat.get_weather", side_effect=fake_weather), \
             patch("app.api.routes.chat.generate_weather_response", side_effect=fake_llm):

            # --- Turn 1: "What's the weather in Hyderabad?" ---
            history = []
            resp1 = await ac.post("/api/v1/chat", json={
                "message": "What's the weather in Hyderabad?",
                "history": history,
            })
            assert resp1.status_code == 200, resp1.text
            data1 = resp1.json()
            assert "Hyderabad" in data1["location"]
            assert "Hyderabad" in data1["answer"]

            # Update client-side history
            history.append({"role": "user", "content": "What's the weather in Hyderabad?"})
            history.append({"role": "assistant", "content": data1["answer"]})

            # --- Turn 2: "What about tomorrow?" ---
            history_turn2 = list(history)
            history_turn2.append({"role": "user", "content": "What about tomorrow?"})
            resp2 = await ac.post("/api/v1/chat", json={
                "message": "What about tomorrow?",
                "history": history_turn2,
            })
            assert resp2.status_code == 200, resp2.text
            data2 = resp2.json()
            assert "Hyderabad" in data2["location"]
            assert "Hyderabad" in data2["answer"]

            # Update client-side history
            history.append({"role": "user", "content": "What about tomorrow?"})
            history.append({"role": "assistant", "content": data2["answer"]})

            # --- Turn 3: "What about Chennai?" ---
            history_turn3 = list(history)
            history_turn3.append({"role": "user", "content": "What about Chennai?"})
            resp3 = await ac.post("/api/v1/chat", json={
                "message": "What about Chennai?",
                "history": history_turn3,
            })
            assert resp3.status_code == 200, resp3.text
            data3 = resp3.json()
            assert "Chennai" in data3["location"]
            assert "Chennai" in data3["answer"]

            # Update client-side history
            history.append({"role": "user", "content": "What about Chennai?"})
            history.append({"role": "assistant", "content": data3["answer"]})

            # --- Turn 4: "Will it be safe to travel there?" ---
            history_turn4 = list(history)
            history_turn4.append({"role": "user", "content": "Will it be safe to travel there?"})
            resp4 = await ac.post("/api/v1/chat", json={
                "message": "Will it be safe to travel there?",
                "history": history_turn4,
            })
            assert resp4.status_code == 200, resp4.text
            data4 = resp4.json()
            # "there" must resolve to Chennai from Turn 3
            assert "Chennai" in data4["location"]
            assert "Chennai" in data4["answer"]
