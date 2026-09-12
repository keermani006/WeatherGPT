"""
weather_guardrail.py — Lightweight, regex-based check to verify if a user's
chat message is weather-related before calling the LLM.

Prevents prompt-injection / jailbreak attempts and irrelevant queries
from burning LLM API tokens.
"""

import re

# ---------------------------------------------------------------------------
# Layer 1 — Prompt-injection, jailbreak & non-weather task rejection patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[str] = [
    # Jailbreaks and prompt injections
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
    # Programming, coding, and development tasks
    r"\b(?:write|generate|debug|fix|create|show)\b.*?\b(?:code|program|script|function|python|java|javascript|cpp|c\+\+|html|css|sql|app|bot|api)\b",
    r"\b(?:python|javascript|typescript|c\+\+|java|golang|rust|php)\s+(?:code|script|program|function|class)\b",
    r"\bwrite\s+(?:me\s+)?(?:a\s+)?(?:python|java|javascript|c\+\+|code|script|program|function)\b",
    # General non-weather off-topic requests
    r"\b(?:tell\s+me\s+a\s+)?joke\b",
    r"\briddle\b",
    r"\bwrite\s+(?:an?\s+)?(?:essay|poem|song|story|letter|article|assignment|homework)\b",
    r"\bwho\s+is\s+(?:the\s+)?(?:prime\s+minister|president|ceo|king|queen)\b",
    r"\b(?:stock\s+price|cryptocurrency|bitcoin|ethereum)\b",
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
    # Direct weather items & travel safety
    "umbrella", "raincoat",
    "outdoor event", "outdoor activity",
    "drive to", "driving to", "travel to", "travelling to", "traveling to", "trip to", "road trip",
    "safe to travel", "safe to drive", "safe to visit", "safe to go",
]

# WEAK signals — individually ambiguous, only count in combination.
_WEAK_WEATHER: list[str] = [
    "hot", "cold", "warm", "cool", "chilly", "freezing",
    "sun", "sunny", "sunshine",
    "wind", "breeze",
    "rain",
    "cloud",
    "sky",
    "today", "tonight", "tomorrow", "this week", "weekend",
    "morning", "evening", "afternoon", "night", "now",
    "this morning", "this evening", "this afternoon",
    "jacket", "coat",
    "outdoor", "outside",
    "degree",
    "condition", "conditions",
    "wear", "clothes", "outfit",
    "picnic", "hike", "trek", "cycling", "drive", "driving", "travel", "fly", "flight",
    "farm", "farming", "farmer", "agriculture", "crop", "crops",
    "paddy", "cotton", "wheat", "rice", "groundnut", "maize", "chilli",
    "seed", "seeds", "sow", "sowing", "harvest", "harvesting", "plant", "planting",
    "grow", "growing", "irrigation", "irrigate", "soil", "spoilage", "mandi", "fertilizer",
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


def is_weather_related(message: str) -> bool:
    """
    Return True if *message* is a weather-related user request.

    Decision logic (priority order):
    1. Blank message  → False.
    2. Prompt-injection attempt → False (always, regardless of content).
    3. Strong weather keyword present → True.
    4. Two or more weak weather signals → True (ambiguous but likely weather).
    5. Otherwise → False (conservative default).
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
