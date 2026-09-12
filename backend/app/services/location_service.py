"""
app/services/location_service.py

Resolve location coordinates and place name via Nominatim / OpenStreetMap.

Phase 2 additions:
  - TTL caching for geocode and reverse-geocode results (1 hour)
  - Retry with exponential backoff on transient Nominatim failures
  - Circuit breaker to stop hammering Nominatim when it's down
  - Query validation (empty / too-long queries rejected before hitting Nominatim)
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.core.cache import (
    geocode_key,
    location_cache,
    location_search_key,
    reverse_geocode_key,
)
from app.core.config import get_settings
from app.core.resilience import async_retry, cb_nominatim
from app.schemas.chat import RouteWaypoint, TravelCardData, WeatherData
from app.schemas.location import LocationSearchResult

logger = logging.getLogger(__name__)
settings = get_settings()

# Maximum query length to avoid sending uncontrolled traffic to Nominatim
_MAX_QUERY_LENGTH = 100


@dataclass
class ResolvedLocation:
    """Resolved location coordinates and display name."""
    name: str
    latitude: float
    longitude: float
    source: str  # "explicit", "extracted", or "gps"


_DISALLOWED_WORDS = {
    # Pronouns, articles, demonstratives
    "the", "a", "an", "this", "that", "these", "those",
    "my", "your", "our", "their", "his", "her", "its",
    "i", "we", "you", "they", "he", "she", "it", "me", "us", "them",
    # Temporal & filler
    "here", "there", "now", "today", "tomorrow", "tonight",
    "yesterday", "morning", "afternoon", "evening", "night", "weekend", "week", "day",
    # Contractions & short grammatical forms
    "s", "t", "d", "m", "re", "ve", "ll",
    # Common verbs & auxiliaries
    "take", "taking", "bring", "bringing", "carry", "carrying", "transport", "transporting",
    "move", "moving", "send", "sending", "sell", "selling", "buy", "buying",
    "grow", "growing", "grown", "plant", "planting", "sow", "sowing", "harvest", "harvesting",
    "spray", "spraying", "irrigate", "irrigating", "water", "watering",
    "drive", "driving", "travel", "traveling", "travelling", "go", "going", "gone",
    "write", "writing", "tell", "telling", "explain", "explaining",
    "make", "making", "do", "doing", "see", "seeing", "check", "checking",
    "want", "wanting", "need", "needing", "like", "liking", "hope", "hoping",
    "is", "are", "am", "was", "were", "be", "been", "being",
    "have", "has", "had", "can", "could", "will", "would", "shall", "should", "may", "might",
    # Question words
    "what", "how", "why", "who", "when", "where", "which",
    # Weather and environmental words
    "weather", "forecast", "rain", "temperature", "temp", "climate", "conditions", "humidity",
    "precipitation", "wind", "storm", "cloud", "clouds", "sun", "sunny", "fog", "mist",
    "snow", "hail", "alert", "alerts",
    # Agriculture / crop / cargo words
    "crop", "crops", "paddy", "cotton", "wheat", "rice", "groundnut", "maize", "chilli",
    "vegetable", "vegetables", "fruit", "fruits", "seed", "seeds", "soil", "fertilizer",
    "fertilizers", "pesticide", "pesticides", "pest", "pests", "fungus", "yield", "mandi",
    # Non-weather off-topic terms
    "code", "coding", "python", "java", "javascript", "script", "program", "programming",
    "joke", "jokes", "story", "stories", "essay", "homework", "assignment", "problem",
    "question", "questions", "answer", "answers",
    # Qualifiers
    "safe", "safely", "okay", "ok", "good", "bad", "better", "best", "worse", "worst",
    "please", "thanks", "thank",
}

_STOP_WORDS = _DISALLOWED_WORDS


def clean_place_name(name: Optional[str]) -> Optional[str]:
    """Clean and validate an extracted place name candidate."""
    if not name:
        return None
    cleaned = name.strip().rstrip("?.!,-").strip()
    # Strip leading/trailing quotes and punctuation
    cleaned = re.sub(r"^['\"\s,.-]+|['\"\s,.-]+$", "", cleaned)
    # Strip leading articles e.g. "the London" -> "London"
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    # Strip trailing temporal words or qualifiers
    cleaned = re.sub(
        r"\s+(?:tonight|today|tomorrow|now|this\s+\w+|next\s+\w+|safe|safely|considering.*|at\s+.*|around\s+.*|in\s+the\s+.*|in\s+evening.*|in\s+morning.*|in\s+afternoon.*)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned or len(cleaned) < 2:
        return None

    words = [w.lower() for w in re.findall(r"[A-Za-z]+", cleaned)]
    if not words:
        return None
    # Reject if single disallowed word
    if len(words) == 1 and words[0] in _DISALLOWED_WORDS:
        return None
    # Reject if candidate contains any disallowed word
    if any(w in _DISALLOWED_WORDS for w in words):
        return None
    # Reject excessively long candidates
    if len(words) > 4:
        return None

    return cleaned.title()


_ROUTE_LOOKAHEAD = (
    r"(?=\s+(?:at\b|around\b|round\b|about\b|by\b|in\b|on\b|this\b|next\b|"
    r"to\s+[a-z]+|for\b|in\s+order\s+to|during\b|before\b|after\b|while\b|when\b|with\b|"
    r"tonight\b|today\b|tomorrow\b|now\b|safe\b|safely\b|is\b|are\b|will\b|can\b|could\b|should\b|does\b)|\?|[.,;!?]|$)"
)


def extract_route_info(message: str | None) -> tuple[Optional[str], Optional[str]]:
    """
    Extract (origin, destination) from travel/route messages, supporting both explicit
    travel keywords ("drive from A to B") and implicit movement/cargo/purpose scenarios
    ("take my harvested rice to Chennai to sell", "deliver vegetables to Bangalore").
    """
    if not message:
        return None, None
    msg = message.strip()

    # 1. Cargo / implicit movement from X to Y:
    # "taking my rice from Guntur to Chennai to sell", "transport goods from Hyderabad to Pune"
    cargo_from_to = re.search(
        r"\b(?:take|taking|took|bring|bringing|brought|carry|carrying|carried|transport|transporting|transported|"
        r"ship|shipping|shipped|send|sending|sent|move|moving|moved|deliver|delivering|delivered|"
        r"transfer|transferring|transferred|get|getting|got|haul|hauling)\b.+?\bfrom\s+([A-Za-z\s,-]{2,30}?)\s+to\s+([A-Za-z\s,-]{2,30}?)"
        + _ROUTE_LOOKAHEAD,
        msg,
        re.IGNORECASE,
    )
    if cargo_from_to:
        orig = clean_place_name(cargo_from_to.group(1))
        dest = clean_place_name(cargo_from_to.group(2))
        if dest:
            return orig, dest

    # 2. General "from X to Y" (with optional travel/drive verbs)
    from_to_match = re.search(
        r"\b(?:drive|driving|travel|traveling|travelling|ride|riding|commute|commuting|transport|transporting|trucks?|trip|deliver|delivering|route)?\s*"
        r"\bfrom\s+([A-Za-z\s,-]{2,30}?)\s+to\s+([A-Za-z\s,-]{2,30}?)"
        + _ROUTE_LOOKAHEAD,
        msg,
        re.IGNORECASE,
    )
    if from_to_match:
        orig = clean_place_name(from_to_match.group(1))
        dest = clean_place_name(from_to_match.group(2))
        if dest:
            return orig, dest

    # 3. "between X and Y"
    between_match = re.search(
        r"\bbetween\s+([A-Za-z\s,-]{2,30}?)\s+and\s+([A-Za-z\s,-]{2,30}?)"
        + _ROUTE_LOOKAHEAD,
        msg,
        re.IGNORECASE,
    )
    if between_match:
        orig = clean_place_name(between_match.group(1))
        dest = clean_place_name(between_match.group(2))
        if orig and dest:
            return orig, dest

    # 4. Cargo / movement to destination:
    # "take it to Chennai to sell the crop", "need to get my rice to Chennai", "transport vegetables to Pune"
    cargo_to = re.search(
        r"\b(?:take|taking|took|bring|bringing|brought|carry|carrying|carried|transport|transporting|transported|"
        r"ship|shipping|shipped|send|sending|sent|move|moving|moved|deliver|delivering|delivered|"
        r"transfer|transferring|transferred|get|getting|got|haul|hauling)\b.+?\bto\s+([A-Za-z\s,-]{2,30}?)"
        + _ROUTE_LOOKAHEAD,
        msg,
        re.IGNORECASE,
    )
    if cargo_to:
        dest = clean_place_name(cargo_to.group(1))
        if dest:
            return None, dest

    # 5. Destination with travel/movement verb
    dest_patterns = [
        re.compile(
            r"\b(?:travel(?:ling|ing)?|go(?:ing)?|drive|driving|leave|leaving|trip|visit(?:ing)?|flight|head(?:ing)?|walk(?:ing)?|commute|commuting)\s+(?:to|for)\s+([A-Za-z\s,-]{2,30}?)"
            + _ROUTE_LOOKAHEAD,
            re.IGNORECASE,
        ),
        re.compile(
            r"\bvisit(?:ing)?\s+([A-Za-z\s,-]{2,30}?)" + _ROUTE_LOOKAHEAD,
            re.IGNORECASE,
        ),
        re.compile(
            r"\bfrom\s+(?:here|current\s+location|my\s+location)\s+to\s+([A-Za-z\s,]{2,30}?)"
            + _ROUTE_LOOKAHEAD,
            re.IGNORECASE,
        ),
        # Purpose-driven destination: e.g. "going to Chennai to sell rice"
        re.compile(
            r"\bto\s+([A-Za-z\s,-]{2,30}?)\s+(?:to\s+sell|to\s+deliver|to\s+pick\s+up|to\s+buy|to\s+market|to\s+trade)",
            re.IGNORECASE,
        ),
    ]
    for pat in dest_patterns:
        m = pat.search(msg)
        if m:
            dest = clean_place_name(m.group(1))
            if dest:
                return None, dest

    return None, None


def extract_travel_destination(message: str | None) -> Optional[str]:
    """Extract destination place name from travel/route query."""
    _, dest = extract_route_info(message)
    return dest


def detect_cargo(message: str | None) -> Optional[str]:
    """
    Detect cargo or agricultural produce being transported.
    Matches crops, grains, harvested items, perishables, livestock, equipment, etc.
    """
    if not message:
        return None
    msg = message.lower()

    if re.search(r"\b(?:harvested\s+rice|paddy|rice\s+crop|rice)\b", msg):
        return "Harvested Rice / Paddy"
    if re.search(r"\b(?:cotton|kapas)\b", msg):
        return "Cotton"
    if re.search(r"\b(?:vegetables?|tomatoes?|potatoes?|onions?|chil+ies?|greens?|veggies?)\b", msg):
        return "Fresh Vegetables"
    if re.search(r"\b(?:fruits?|mangoes?|bananas?|apples?|oranges?|grapes?)\b", msg):
        return "Fresh Fruits"
    if re.search(r"\b(?:wheat|maize|corn|grain|barley|millet|sorghum|jowar|bajra)\b", msg):
        return "Grains / Cereals"
    if re.search(r"\b(?:pulses?|dal|soya|soybean|lentils?|groundnut|peanuts?)\b", msg):
        return "Pulses / Oilseeds"
    if re.search(r"\b(?:flowers?|floriculture|roses?|jasmine)\b", msg):
        return "Cut Flowers"
    if re.search(r"\b(?:livestock|cattle|cows?|bulls?|buffaloes?|sheep|goats?|poultry|chickens?|animals?)\b", msg):
        return "Livestock / Animals"
    if re.search(r"\b(?:machinery|equipment|tractors?|tools?|materials?|hardware)\b", msg):
        return "Machinery / Equipment"
    if re.search(r"\b(?:crops?|produce|harvest|goods|supplies|consignment|cargo|shipment)\b", msg):
        return "Agricultural Produce / Cargo"

    return None


def detect_departure_timing(message: str | None) -> Optional[str]:
    """
    Extract user-specified departure timing from the query if present.
    Supports:
      - Immediate / current: "now", "right now", "immediately", "currently" -> "Now (Current Time)"
      - Relative time offsets: "in 2 hours", "in an hour", "in 30 mins" -> "In 2 Hours"
      - Clock times with modifiers: "at round 6 in evening", "around 6:30 pm", "at 6 in evening", "at 6pm"
      - Time-of-day periods: "evening", "night", "morning", "afternoon", "tonight", "tomorrow"
    Returns None if no explicit departure time was provided (avoids fabricating times).
    """
    if not message:
        return None
    msg = message.lower()

    # 1. "now", "right now", "immediately", "currently", "at present"
    if re.search(r"\b(?:now|right\s+now|immediately|shortly|at\s+present|currently)\b", msg):
        return "Now (Current Time)"

    # 2. Relative hours / minutes: "in 2 hours", "in an hour", "in 30 minutes"
    rel_match = re.search(r"\bin\s+(\d+|an?|half\s+an?)\s*(hours?|hrs?|mins?|minutes?)\b", msg)
    if rel_match:
        val, unit = rel_match.group(1), rel_match.group(2)
        if val in ("a", "an"):
            val = "1"
        elif "half" in val:
            val = "0.5"
        unit_str = "Hour" if "h" in unit and val == "1" else ("Hours" if "h" in unit else "Minutes")
        return f"In {val} {unit_str}"

    # 3. Explicit clock time with optional prefix & suffix
    # e.g. "at round 6 in evening", "around 6:30 pm", "at 6 in the morning", "by 7 pm", "at 18:00"
    time_pat = re.search(
        r"\b(?:at\s+(?:a\s+)?round|around|round|about|by|at)\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
        r"(?:\s*(?:in|during|at|of)?\s*(?:the\s+)?(evening|night|morning|afternoon))?\b",
        msg,
    )
    if time_pat:
        hour = int(time_pat.group(1))
        minute = time_pat.group(2) or "00"
        meridiem = (time_pat.group(3) or "").replace(".", "").upper()
        period = (time_pat.group(4) or "").lower()

        prefix_full = time_pat.group(0).lower()
        if any(w in prefix_full for w in ["round", "around", "about"]):
            pref = "Around"
        elif "by" in prefix_full:
            pref = "By"
        else:
            pref = "At"

        if not meridiem:
            if period in ("evening", "night") and hour < 12:
                meridiem = "PM"
            elif period in ("morning",) and hour < 12:
                meridiem = "AM"
            elif period in ("afternoon",) and hour < 12:
                meridiem = "PM"
            elif hour >= 12:
                if hour > 12:
                    hour -= 12
                meridiem = "PM"
            else:
                if "evening" in msg or "night" in msg or "tonight" in msg:
                    meridiem = "PM"
                elif "morning" in msg:
                    meridiem = "AM"

        time_str = f"{hour}:{minute} {meridiem}".strip()
        period_str = f" ({period.title()})" if period else ""
        return f"{pref} {time_str}{period_str}"

    # 4. Standalone time like "6pm" or "6:30pm" without "at"
    time_pat_standalone = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
        msg,
    )
    if time_pat_standalone:
        hour = int(time_pat_standalone.group(1))
        minute = time_pat_standalone.group(2) or "00"
        meridiem = (time_pat_standalone.group(3) or "").replace(".", "").upper()
        return f"At {hour}:{minute} {meridiem}"

    # 5. Day & period combinations
    day_periods = [
        (r"\b(?:tomorrow\s+early\s+morning|tomorrow\s+morning)\b", "Tomorrow Morning"),
        (r"\b(?:tomorrow\s+afternoon)\b", "Tomorrow Afternoon"),
        (r"\b(?:tomorrow\s+evening)\b", "Tomorrow Evening"),
        (r"\b(?:tomorrow\s+night)\b", "Tomorrow Night"),
        (r"\b(?:tomorrow)\b", "Tomorrow"),
        (r"\b(?:tonight|this\s+night|(?:at|in|by|during)\s+(?:the\s+)?night|\bnight)\b", "Tonight"),
        (r"\b(?:today\s+evening|this\s+evening|(?:at|in|by|during)\s+(?:the\s+)?evening|\bevening)\b", "This Evening"),
        (r"\b(?:today\s+afternoon|this\s+afternoon|(?:at|in|by|during)\s+(?:the\s+)?afternoon|\bafternoon)\b", "This Afternoon"),
        (r"\b(?:today\s+morning|this\s+morning|early\s+morning|(?:at|in|by|during)\s+(?:the\s+)?morning|\bmorning)\b", "This Morning"),
        (r"\b(?:this\s+weekend|on\s+saturday|on\s+sunday|on\s+monday|on\s+tuesday|on\s+wednesday|on\s+thursday|on\s+friday)\b", "Upcoming Scheduled Day"),
    ]
    for pat, label in day_periods:
        if re.search(pat, msg):
            return label

    return None


def compute_travel_risk(
    origin_name: str,
    origin_weather: Optional[WeatherData],
    destination_name: str,
    destination_weather: Optional[WeatherData],
    waypoints: List[RouteWaypoint],
    cargo: Optional[str] = None,
    departure_timing: Optional[str] = None,
) -> TravelCardData:
    """
    Compute structured Travel Weather Card data evaluating overall risk, route weather,
    timing constraints, cargo protection, and practical recommendations.
    Never fabricates unsupported route weather or departure times.
    """
    # 1. Collect all points along corridor
    all_points: list[tuple[str, WeatherData]] = []
    if origin_weather:
        all_points.append((origin_weather.location or origin_name, origin_weather))
    for wp in waypoints:
        if wp.weather:
            all_points.append((wp.name, wp.weather))
    if destination_weather:
        all_points.append((destination_weather.location or destination_name, destination_weather))

    # 2. Risk analysis across route
    rainy_points = []
    heavy_rain_points = []
    fog_points = []
    windy_points = []
    max_rain_prob = 0.0

    for name, w in all_points:
        rp = w.rain_probability or 0.0
        if rp > max_rain_prob:
            max_rain_prob = rp
        cond = (w.condition or "").lower()
        rf = w.rainfall or 0.0

        if rp >= 60.0 or rf >= 4.0 or any(s in cond for s in ["heavy", "thunderstorm", "storm", "squall", "torrential"]):
            heavy_rain_points.append(name)
        elif rp >= 25.0 or rf >= 1.0 or any(s in cond for s in ["rain", "drizzle", "shower"]):
            rainy_points.append(name)

        if (w.humidity or 0) >= 80 and (w.wind_speed or 5.0) <= 2.5:
            fog_points.append(name)
        if (w.wind_speed or 0.0) >= 12.0:
            windy_points.append(name)

    # Determine overall travel weather risk: Low | Moderate | High
    if heavy_rain_points or max_rain_prob >= 60.0:
        overall_risk = "High"
        risk_summary = f"High precipitation / storm risk along corridor (up to {max_rain_prob:.0f}% chance of rain)."
    elif rainy_points or max_rain_prob >= 25.0 or fog_points or windy_points:
        overall_risk = "Moderate"
        reasons = []
        if max_rain_prob >= 25.0:
            reasons.append(f"light/moderate rain chances up to {max_rain_prob:.0f}%")
        if fog_points:
            reasons.append(f"reduced visibility / fog near {', '.join(fog_points[:2])}")
        if windy_points:
            reasons.append(f"crosswinds near {', '.join(windy_points[:2])}")
        risk_summary = f"Moderate travel risk: {'; '.join(reasons)}."
    else:
        overall_risk = "Low"
        risk_summary = f"Favorable weather conditions with low rain probability (maximum {max_rain_prob:.0f}%) along the route."

    # 3. Route weather summary distinguishing rain intensity
    route_sections = []
    if heavy_rain_points:
        route_sections.append(f"Heavy rain / thunderstorms expected around {', '.join(heavy_rain_points)}")
    if rainy_points:
        route_sections.append(f"Light to moderate showers expected near {', '.join(rainy_points)}")
    if fog_points and not (heavy_rain_points or rainy_points):
        route_sections.append(f"Dense fog patches expected near {', '.join(fog_points)}")
    if not route_sections:
        route_sections.append("Dry and clear conditions throughout the route corridor")
    route_weather_summary = ". ".join(route_sections) + "."

    # 4. Timing evaluation
    if departure_timing:
        if "Now" in departure_timing:
            timing_note = "Evaluated for immediate departure (Current conditions). Real-time weather reflects your travel window now."
        else:
            timing_note = f"Evaluated for planned departure: {departure_timing}. Forecast matches this expected travel window."
    else:
        timing_note = "No departure time specified; evaluated based on upcoming forecast. Weather risk depends on your exact departure timing."

    # 5. Cargo-specific risk advice
    cargo_risk_advice = None
    if cargo:
        if "rice" in cargo.lower() or "paddy" in cargo.lower() or "grain" in cargo.lower():
            cargo_risk_advice = (
                "Harvested rice and grains are highly vulnerable to moisture absorption, swelling, and fungal mold. "
                "The critical issue is preventing rainwater from penetrating the grain sacks. Ensure cargo is transported under "
                "heavy-duty waterproof tarpaulins with all seams and edges tightly tied down, and elevate bottom sacks on "
                "pallets or plastic lining to prevent water pooling on the truck bed."
            )
        elif "vegetable" in cargo.lower() or "fruit" in cargo.lower():
            cargo_risk_advice = (
                "Fresh produce is perishable and easily spoiled by trapped moisture and transit delays. Ensure waterproof "
                "coverings have adequate cross-ventilation to prevent condensation buildup and rotting."
            )
        elif "cotton" in cargo.lower():
            cargo_risk_advice = (
                "Raw cotton rapidly absorbs ambient moisture and road spray, causing staining and grade depreciation. "
                "Use full waterproof wrapping and secure underside flaps against road splash."
            )
        elif "livestock" in cargo.lower() or "animal" in cargo.lower():
            cargo_risk_advice = (
                "Ensure transport vehicle has adequate ventilation, non-slip flooring, and side protection against rain "
                "and cold wind chill during transit."
            )
        elif "machinery" in cargo.lower() or "equipment" in cargo.lower():
            cargo_risk_advice = (
                "Protect sensitive mechanical joints and electrical systems with waterproof canvas or heavy tarps to prevent rust."
            )
        else:
            cargo_risk_advice = (
                "Protect cargo against rain intrusion using tightly tied waterproof tarpaulins, and ensure bottom layers are protected from spray."
            )

    # 6. Practical recommendation (concise, non-fabricated)
    all_adverse = heavy_rain_points + rainy_points
    if overall_risk == "High":
        if cargo:
            recommendation = (
                f"Consider delaying departure if possible due to heavy rain expected along the route. "
                f"If travel is necessary, ensure {cargo} is fully waterproofed and drive with caution."
            )
        else:
            recommendation = (
                "Consider delaying travel or allow extra journey time; significant rain/storm activity is expected along the route."
            )
    elif overall_risk == "Moderate":
        adverse_str = f" around {', '.join(all_adverse[:2])}" if all_adverse else ""
        if cargo:
            recommendation = (
                f"Travel appears reasonable, but rain is expected{adverse_str}; keep the {cargo} fully waterproofed."
            )
        else:
            recommendation = (
                f"Travel appears reasonable, but expect wet road sections{adverse_str}. Allow extra travel time."
            )
    else:
        recommendation = "Favorable travel conditions along the entire route corridor. Safe to proceed as planned."

    orig_disp = origin_name or (origin_weather.location if origin_weather else "Origin")
    dest_disp = destination_name or (destination_weather.location if destination_weather else "Destination")

    return TravelCardData(
        origin=orig_disp,
        destination=dest_disp,
        overall_risk=overall_risk,
        risk_summary=risk_summary,
        route_weather_summary=route_weather_summary,
        departure_timing=departure_timing,
        timing_note=timing_note,
        cargo=cargo,
        cargo_risk_advice=cargo_risk_advice,
        recommendation=recommendation,
        waypoints=waypoints,
    )


def extract_locations_from_message(message: str | None) -> list[str]:
    """Extract all candidate place names mentioned in the message."""
    if not message:
        return []
    cleaned = message.strip()

    # 1. Route locations take priority (e.g. from Hyderabad to Pune or take crops to anantapur)
    orig, dest = extract_route_info(cleaned)
    results = []
    if orig:
        results.append(orig)
    if dest and dest not in results:
        results.append(dest)
    if results:
        return results

    found: list[str] = []

    # 2. Preposition patterns: in/at/for/around/near/about/of/to <Place> (Title Case first)
    prep_matches = re.finditer(
        r"\b(?:in|at|for|around|near|about|of|to)\s+([A-Z][A-Za-z\s,-]+?)(?=[.,;!?]|\s+(?:today|tomorrow|tonight|now|this|next|how|will|what|where|why|and|or|is|are|with|considering|weather|forecast)|$)",
        cleaned,
    )
    for m in prep_matches:
        loc = clean_place_name(m.group(1))
        if loc and loc not in found:
            found.append(loc)
    if found:
        return found

    # 3. Pattern: <Place> (weather|forecast|climate|temperature|conditions|rain)
    place_first = re.finditer(
        r"\b([A-Za-z][A-Za-z\s,-]{1,30}?)\s+(?:weather|forecast|climate|temperature|conditions|rain|radar|humidity)\b",
        cleaned,
        re.IGNORECASE,
    )
    for m in place_first:
        loc = clean_place_name(m.group(1))
        if loc and loc not in found:
            found.append(loc)
    if found:
        return found

    # 4. Fallback case-insensitive preposition (e.g. 'in mumbai', 'about delhi', 'to anantapur')
    eos_match = re.finditer(
        r"\b(?:in|at|for|around|near|about|of|to)\s+([A-Za-z\s,-]+?)(?=[.,;!?]|\s+(?:today|tomorrow|tonight|now|this|next|how|will|what|where|why|and|or|is|are|with|considering|weather|forecast)|$)",
        cleaned,
        re.IGNORECASE,
    )
    for m in eos_match:
        loc = clean_place_name(m.group(1))
        if loc and loc not in found:
            found.append(loc)
    if found:
        return found

    # 5. Standalone words (e.g. 'Tokyo', 'New York', 'Mumbai')
    stripped = re.sub(
        r"\b(?:weather|forecast|today|tomorrow|tonight|now|please|tell|me)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip("?.! ")
    if stripped and len(stripped.split()) <= 3:
        loc = clean_place_name(stripped)
        if loc:
            return [loc]

    return []


def extract_location_from_message(message: str | None) -> Optional[str]:
    """Extract primary place name from user message."""
    locs = extract_locations_from_message(message)
    return locs[0] if locs else None






async def geocode_location(place_name: str) -> tuple[float, float, str]:
    """
    Geocode a place name into (latitude, longitude, display_name) using Nominatim.
    Results are cached for 1 hour.
    """
    place_name = place_name.strip()[:_MAX_QUERY_LENGTH]
    cache_key = geocode_key(place_name)

    cached = location_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"q": place_name, "format": "json", "limit": 1}
    url = f"{settings.nominatim_base_url.rstrip('/')}/search"

    async def _do_request():
        async with cb_nominatim:
            async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

    logger.info("Geocoding place '%s' via Nominatim", place_name)
    data = await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )

    if not data:
        logger.warning("Nominatim found no matches for '%s'", place_name)
        raise ValueError(f"Location not found: '{place_name}'")

    first = data[0]
    lat = float(first["lat"])
    lon = float(first["lon"])
    display_name = (
        first.get("name") or first.get("display_name", place_name).split(",")[0].strip()
    )
    logger.info("Nominatim resolved '%s' → lat=%.4f, lon=%.4f", place_name, lat, lon)

    result = (lat, lon, display_name)
    location_cache.set(cache_key, result, settings.location_cache_ttl)
    return result


async def reverse_geocode(latitude: float, longitude: float) -> str:
    """
    Reverse geocode GPS coordinates to a human-friendly place name.
    Results are cached for 1 hour.
    Falls back gracefully on failure.
    """
    cache_key = reverse_geocode_key(latitude, longitude)
    cached = location_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"lat": latitude, "lon": longitude, "format": "json"}
    url = f"{settings.nominatim_base_url.rstrip('/')}/reverse"

    try:
        async def _do_request():
            async with cb_nominatim:
                async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    resp.raise_for_status()
                    return resp.json()

        data = await async_retry(
            _do_request,
            max_attempts=2,  # fewer retries for reverse geocode (best-effort)
            base_delay=settings.retry_base_delay,
        )
        address = data.get("address", {})
        name = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("suburb")
            or address.get("county")
            or data.get("name")
        )
        if name:
            location_cache.set(cache_key, name, settings.location_cache_ttl)
            return name
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nominatim reverse geocoding failed (%s); using coordinate fallback", exc)

    fallback = f"Current Location ({latitude:.2f}, {longitude:.2f})"
    return fallback


async def search_locations(query: str, limit: int = 5) -> List[LocationSearchResult]:
    """
    Search places matching query via Nominatim /search.
    Results are cached for 1 hour.
    """
    cleaned = query.strip()[:_MAX_QUERY_LENGTH]
    if not cleaned:
        return []

    cache_key = location_search_key(cleaned, limit)
    cached = location_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {
        "q": cleaned,
        "format": "json",
        "limit": max(1, min(limit, 10)),
        "addressdetails": 1,
    }
    url = f"{settings.nominatim_base_url.rstrip('/')}/search"

    async def _do_request():
        async with cb_nominatim:
            async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

    logger.info("Searching places for '%s' via Nominatim (limit=%d)", cleaned, limit)
    data = await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )

    results: List[LocationSearchResult] = []
    for item in data:
        display = item.get("name") or item.get("display_name", "").split(",")[0].strip()
        address = item.get("address", {})
        country = address.get("country", "")
        try:
            results.append(LocationSearchResult(
                name=display,
                country=country,
                latitude=float(item["lat"]),
                longitude=float(item["lon"]),
            ))
        except (KeyError, ValueError):
            continue

    location_cache.set(cache_key, results, settings.location_cache_ttl)
    return results


async def resolve_location(
    explicit: Optional[str] = None,
    message: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    prefer_message: bool = True,
) -> Optional[ResolvedLocation]:
    """
    Resolve coordinates following priority rules:
      1. If prefer_message and message contains a specific place or route origin,
         resolve that location.
      2. Explicit location from request (ignoring placeholder labels).
      3. Extracted location from message (if not checked in step 1).
      4. Browser GPS coordinates.
      5. Neither available → return None.
    """
    _PLACEHOLDER_NAMES = {
        "current location", "current", "my location", "here",
        "device location", "gps", "unknown", "string"
    }

    clean_explicit = explicit.strip() if explicit else ""
    if clean_explicit.lower() in _PLACEHOLDER_NAMES:
        clean_explicit = ""

    if prefer_message and message:
        route_orig, route_dest = extract_route_info(message)
        # If query specifies a destination (e.g. 'take crops to Anantapur') and the user has
        # GPS coordinates or explicit location, preserve GPS/explicit as origin (primary)
        # while chat.py handles route_dest as the travel destination.
        if route_dest and not route_orig and (latitude is not None and longitude is not None or clean_explicit):
            pass  # fall through to explicit/GPS for origin resolution
        else:
            extracted = route_orig or extract_location_from_message(message)
            if extracted:
                lat, lon, display_name = await geocode_location(extracted)
                return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="extracted")

    if clean_explicit:
        loc_name = clean_explicit[:_MAX_QUERY_LENGTH]
        lat, lon, display_name = await geocode_location(loc_name)
        return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="explicit")

    if message and not prefer_message:
        extracted = extract_location_from_message(message)
        if extracted:
            lat, lon, display_name = await geocode_location(extracted)
            return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="extracted")

    if latitude is not None and longitude is not None:
        display_name = await reverse_geocode(latitude, longitude)
        return ResolvedLocation(name=display_name, latitude=latitude, longitude=longitude, source="gps")

    logger.info("No explicit location or browser GPS coordinates available")
    return None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on the Earth in km."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


async def get_route_waypoints(
    orig_lat: float,
    orig_lon: float,
    dest_lat: float,
    dest_lon: float,
    max_waypoints: int = 3,
) -> list[dict]:
    """
    Find 1 to 3 intermediate transit waypoints along the travel corridor between
    origin and destination.
    Returns list of dicts: {"name": str, "latitude": float, "longitude": float, "distance_km": float}
    """
    total_dist = haversine_distance(orig_lat, orig_lon, dest_lat, dest_lon)
    if total_dist < 40.0:
        return []

    # Decide number of waypoints based on distance
    if total_dist < 150.0:
        num_wp = 1
    elif total_dist < 400.0:
        num_wp = min(2, max_waypoints)
    else:
        num_wp = min(3, max_waypoints)

    route_coords: list[tuple[float, float]] = []

    # 1. Try fetching driving route geometry from OSRM
    url = f"https://router.project-osrm.org/route/v1/driving/{orig_lon},{orig_lat};{dest_lon},{dest_lat}?overview=simplified&geometries=geojson"
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(url, headers={"User-Agent": settings.nominatim_user_agent})
            if resp.status_code == 200:
                data = resp.json()
                routes = data.get("routes", [])
                if routes and "geometry" in routes[0]:
                    coords = routes[0]["geometry"].get("coordinates", [])
                    if len(coords) >= num_wp + 2:
                        route_coords = [(pt[1], pt[0]) for pt in coords]
    except Exception as exc:  # noqa: BLE001
        logger.warning("OSRM route fetch failed (%s); using geometric interpolation", exc)

    # 2. Fallback: linear interpolation
    if not route_coords:
        route_coords = [
            (
                orig_lat + (dest_lat - orig_lat) * (i / (num_wp + 1)),
                orig_lon + (dest_lon - orig_lon) * (i / (num_wp + 1)),
            )
            for i in range(num_wp + 2)
        ]

    step = len(route_coords) / (num_wp + 1)
    sampled_points = [
        route_coords[int(i * step)]
        for i in range(1, num_wp + 1)
    ]

    waypoints: list[dict] = []
    seen_names = set()

    for lat, lon in sampled_points:
        try:
            place_name = await reverse_geocode(lat, lon)
            clean_name = re.sub(r"\(.*?\)", "", place_name).strip()
            if not clean_name or clean_name.lower() in seen_names:
                continue
            seen_names.add(clean_name.lower())
            dist = haversine_distance(orig_lat, orig_lon, lat, lon)
            waypoints.append({
                "name": clean_name,
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(dist, 1),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to reverse geocode waypoint (%f, %f): %s", lat, lon, exc)

    return waypoints

