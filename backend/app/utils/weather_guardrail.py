"""
app/utils/weather_guardrail.py

Single responsibility: decide whether a user message is weather-related.

Two-layer approach:
  1. Hard-reject prompt-injection / jailbreak attempts (always first).
  2. Keyword match against a curated weather vocabulary.
     — Ambiguous single words (today, hot, cool, sky …) only pass
       if they appear alongside a second weather signal or a place name
       pattern, so "What's hot on Netflix?" doesn't slip through.

The function `is_weather_related(message)` is the only public interface.
"""

import re

# ---------------------------------------------------------------------------
# Layer 1 — Prompt-injection / jailbreak rejection patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[str] = [
    r"ignore\s+(your\s+)?(previous\s+|all\s+)?(instructions?|rules?|prompts?|guidelines?)",
    r"ignore\s+the\s+\w+(\s+\w+)?\s+(data|context|prompt)",
    r"forget\s+(your\s+)?(instructions?|rules?)",
    r"reveal\s+(your\s+)?(system\s+prompt|instructions?|api\s+key)",
    r"pretend\s+(you\s+(are|r)|to\s+be)",
    r"act\s+as\s+(a\s+)?(different|new|gpt|chat)",
    r"you\s+are\s+now\s+(a\s+)?\w+",         # "you are now DAN"
    r"override\s+(your\s+)?(instructions?|rules?)",
    r"\bdan\b.*mode",                          # DAN, DUDE, etc.
    r"developer\s+mode",
    r"jailbreak",
    r"do\s+anything\s+now",
    r"disregard\s+(your\s+)?(instructions?|rules?|context|system)",
    r"bypass\s+(your\s+)?(instructions?|rules?|restrictions?|safety)",
    r"(you\s+are|you're)\s+no\s+longer",
    r"new\s+persona",
    r"without\s+(any\s+)?(restriction|filter|limit)",
    r"tell\s+me\s+(your\s+)?(prompt|secret|api\s+key)",
]

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def _contains_injection_attempt(message: str) -> bool:
    """Return True if the message looks like a prompt-injection / jailbreak attempt."""
    return any(p.search(message) for p in _INJECTION_RE)


# ---------------------------------------------------------------------------
# Layer 2 — Weather keyword vocabulary
# ---------------------------------------------------------------------------

# STRONG signals — any one of these alone is enough to pass the message.
_STRONG_WEATHER: list[str] = [
    # Core meteorology
    "weather", "forecast", "climate", "temperature", "rainfall", "precipitation",
    "rain", "raining", "rainy", "drizzle", "shower", "downpour",
    "snow", "snowfall", "snowing", "snowy", "blizzard", "hail",
    "thunderstorm", "thunder", "lightning",
    "wind speed", "wind direction", "windy", "gust",
    "humidity", "dew point", "heat index", "feels like",
    "cloud cover", "cloudy", "overcast",
    "fog", "foggy", "mist", "misty",
    "heatwave", "heat wave", "cold wave",
    "uv index", "ultraviolet",
    "air quality", "aqi",
    "barometric", "pressure",
    "visibility",
    "celsius", "fahrenheit",
    # Severe / alert
    "cyclone", "hurricane", "typhoon", "tornado", "monsoon",
    "flood", "flooding", "drought",
    "storm surge", "warning", "advisory", "weather alert",
    # Specific request patterns
    "what's the weather", "how's the weather", "weather like",
    "weather in", "weather for", "weather at",
    "will it rain", "chance of rain", "rain probability",
    "is it going to", "going to rain", "going to snow",
    "7-day", "7 day", "weekly forecast", "10-day",
]

# WEAK signals — individually ambiguous, only count in combination.
_WEAK_WEATHER: list[str] = [
    "hot", "cold", "warm", "cool", "chilly", "freezing",
    "sun", "sunny", "sunshine",
    "wind", "breeze",
    "rain",          # alone can appear in "brain", "train", etc. — handled below
    "cloud",
    "sky",
    "today", "tonight", "tomorrow", "this week", "weekend",
    "this morning", "this evening", "this afternoon",
    "umbrella", "raincoat",
    "outdoor", "outside",
    "degree",
    "condition", "conditions",
    "wear", "clothes", "outfit",
    "picnic", "hike", "trek", "cycling", "drive", "fly", "flight",
    "farm", "crop", "paddy", "irrigation", "harvest",
]


def _strong_match(lower: str) -> bool:
    """True if any strong weather phrase appears as a standalone word/phrase."""
    for phrase in _STRONG_WEATHER:
        if phrase in lower:
            return True
    return False


def _weak_count(lower: str) -> int:
    """Count how many distinct weak weather signals appear in the message."""
    return sum(1 for w in _WEAK_WEATHER if re.search(r"\b" + re.escape(w) + r"\b", lower))


def _has_location_with_weather_context(lower: str) -> bool:
    """
    Check if the message pairs a location indicator with a question word
    that implies a weather query (e.g. 'weather in Delhi', 'rain Mumbai').
    """
    weather_context_re = re.compile(
        r"\b(weather|rain|forecast|temperature|climate|storm|flood|fog|wind|cloud|humid|snow)\b",
        re.IGNORECASE,
    )
    return bool(weather_context_re.search(lower))


def is_weather_related(message: str) -> bool:
    """
    Return True if *message* is a weather-related user request.

    Decision logic (priority order):
    1. Blank message  → False.
    2. Prompt-injection attempt → False (always, regardless of content).
    3. Strong weather keyword present → True.
    4. Two or more weak weather signals → True (ambiguous but likely weather).
    5. Otherwise → False (conservative default).

    IMPORTANT: This function is called BEFORE any location-bypass logic.
    The caller in chat.py must NOT skip this check based on location detection;
    location alone does not make a question weather-related.
    """
    if not message or not message.strip():
        return False

    if _contains_injection_attempt(message):
        return False

    lower = message.lower()

    if _strong_match(lower):
        return True

    # Two or more weak signals → accept (e.g. "Is tomorrow cold?" has 2 signals)
    if _weak_count(lower) >= 2:
        return True

    return False
