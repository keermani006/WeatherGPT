"""
app/utils/weather_guardrail.py

Single responsibility: decide whether a user message is weather-related.

Phase 1 approach
────────────────
A two-layer check:
  1. Fast keyword / intent scan (free, deterministic, instant).
  2. If layer 1 is inconclusive the message is flagged as unrelated
     (conservative — avoids calling the LLM just for classification).

This keeps cost zero for guardrail decisions while being accurate enough
for the clearly non-weather messages we need to reject.

The function `is_weather_related(message)` is the only public interface.
In a later phase you can swap the internals (e.g., add an LLM call for
edge cases) without changing the callers.
"""

import re

# ---------------------------------------------------------------------------
# Keywords and patterns
# ---------------------------------------------------------------------------

# Phrases that strongly indicate a weather-related question.
_WEATHER_PHRASES: list[str] = [
    # Core weather words
    "weather", "forecast", "climate", "temperature", "temp",
    "rain", "rainfall", "raining", "rainy", "drizzle",
    "snow", "snowfall", "snowing", "snowy", "blizzard",
    "wind", "windy", "breeze", "gust",
    "storm", "thunderstorm", "thunder", "lightning", "hail",
    "humid", "humidity", "dew point",
    "cloud", "cloudy", "overcast", "sunny", "sunshine", "sun",
    "fog", "foggy", "mist", "misty",
    "hot", "cold", "warm", "cool", "chilly", "freezing",
    "heat wave", "heatwave", "heat index",
    "uv index", "uv", "ultraviolet",
    "air quality", "aqi", "pollution",
    "pressure", "barometric",
    "visibility",
    "feels like",
    "degree", "celsius", "fahrenheit",
    # Practical weather intent
    "umbrella", "raincoat", "jacket", "coat",
    "outdoor", "outside", "outing", "picnic", "hike", "trek",
    "flood", "flooding", "drought",
    "cyclone", "hurricane", "typhoon", "tornado", "monsoon",
    # Time phrases common in weather questions
    "today", "tonight", "tomorrow", "this week", "weekend",
    "this morning", "this evening", "this afternoon",
    # Sky / atmospheric
    "sky", "atmosphere", "precipitation",
    # Alert words
    "warning", "alert", "advisory",
]

# Patterns that almost certainly indicate a non-weather request.
# We check these FIRST so an attacker can't hide a prompt injection
# inside a weather-sounding sentence.
_REJECTION_PATTERNS: list[str] = [
    r"ignore\s+(your\s+)?(previous\s+|all\s+)?(instructions?|rules?|prompts?|guidelines?)",
    r"ignore\s+the\s+\w+(\s+\w+)?\s+(data|context|prompt)",  # "ignore the weather data"
    r"forget\s+(your\s+)?(instructions?|rules?)",
    r"reveal\s+(your\s+)?(system\s+prompt|instructions?|api\s+key)",
    r"pretend\s+(you\s+are|to\s+be)",
    r"act\s+as\s+(a\s+)?(different|new)",
    r"override\s+(your\s+)?(instructions?|rules?)",
    r"jailbreak",
    r"do\s+anything\s+now",  # DAN prompt
    r"disregard\s+(your\s+)?(instructions?|rules?|context)",
    r"bypass\s+(your\s+)?(instructions?|rules?|restrictions?|safety)",
]

# Compile once at import time.
_REJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _REJECTION_PATTERNS]


def _contains_injection_attempt(message: str) -> bool:
    """Return True if the message looks like a prompt-injection attempt."""
    return any(pattern.search(message) for pattern in _REJECTION_RE)


def _contains_weather_keyword(message: str) -> bool:
    """Return True if the message contains at least one weather keyword/phrase."""
    lower = message.lower()
    return any(phrase in lower for phrase in _WEATHER_PHRASES)


def is_weather_related(message: str) -> bool:
    """
    Return True if *message* is a weather-related user request.

    Decision logic (in priority order):
    1. Prompt-injection / override attempts → always rejected (False).
    2. Contains a recognised weather keyword/phrase → accepted (True).
    3. Otherwise → rejected (False).

    This is intentionally conservative: ambiguous messages that contain
    no weather signal are rejected.  The approach avoids:
    - False positives (treating "who is the prime minister?" as weather)
    - LLM calls just for classification (cost and latency)

    Parameters
    ----------
    message:
        The raw user message string.

    Returns
    -------
    bool
        True  → weather-related, proceed with pipeline.
        False → reject, return guardrail response.
    """
    if not message or not message.strip():
        return False

    # Hard stop for injection attempts regardless of other content.
    if _contains_injection_attempt(message):
        return False

    return _contains_weather_keyword(message)
