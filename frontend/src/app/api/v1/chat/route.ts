/* POST /api/v1/chat */

import { NextRequest, NextResponse } from "next/server";

interface ChatRequestBody {
  message: string;
  lat?: number;
  lng?: number;
  location_name?: string;
  history?: { role: string; content: string }[];
}

export async function POST(req: NextRequest) {
  let body: ChatRequestBody;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_REQUEST", message: "Invalid JSON body" } } },
      { status: 400 }
    );
  }

  const { message, lat, lng, location_name } = body;

  if (!message || message.trim().length === 0) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_REQUEST", message: "message is required" } } },
      { status: 400 }
    );
  }

  // Check if the message is weather-related
  const weatherKeywords = [
    "weather", "temperature", "rain", "forecast", "humidity", "wind",
    "climate", "hot", "cold", "sunny", "cloudy", "storm", "snow",
    "heat", "warm", "cool", "precipitation", "pressure", "UV",
    "today", "tomorrow", "week", "celsius", "fahrenheit", "degree",
    "monsoon", "fog", "haze", "mist", "thunder", "lightning",
  ];

  const lowerMsg = message.toLowerCase();
  const isWeatherRelated = weatherKeywords.some((kw) => lowerMsg.includes(kw));

  // Check for location mentions in the message
  const mentionedLocation = extractLocationFromMessage(message);

  // If the question is off-topic, return guardrail
  if (!isWeatherRelated && !mentionedLocation) {
    return NextResponse.json(
      {
        detail: {
          error: {
            code: "WEATHER_GUARDRAIL_TRIGGERED",
            message:
              "I can only help with weather-related questions. Try asking about the forecast, temperature, or conditions for a location.",
          },
        },
      },
      { status: 403 }
    );
  }

  // Determine which coordinates to use
  let useLat = lat;
  let useLng = lng;
  let useLocationName = location_name ?? "your location";

  // If user mentioned a specific place, geocode it
  if (mentionedLocation) {
    try {
      const geoRes = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(mentionedLocation)}&count=1&language=en`
      );
      if (geoRes.ok) {
        const geoData = await geoRes.json();
        if (geoData.results?.[0]) {
          useLat = geoData.results[0].latitude;
          useLng = geoData.results[0].longitude;
          useLocationName = geoData.results[0].name;
        } else {
          return NextResponse.json(
            { detail: { error: { code: "LOCATION_NOT_FOUND", message: `Couldn't find "${mentionedLocation}". Try a different city name.` } } },
            { status: 404 }
          );
        }
      }
    } catch {
      return NextResponse.json(
        { detail: { error: { code: "WEATHER_API_ERROR", message: "Couldn't look up that location right now." } } },
        { status: 502 }
      );
    }
  }

  // If we still have no coordinates, ask for location
  if (useLat == null || useLng == null) {
    return NextResponse.json(
      {
        detail: {
          error: {
            code: "LOCATION_REQUIRED",
            message:
              "I need a location to check the weather. You can search for a city in the location bar, enable location access, or mention a place in your message.",
          },
        },
      },
      { status: 422 }
    );
  }

  // Fetch current weather
  try {
    const weatherUrl = `https://api.open-meteo.com/v1/forecast?latitude=${useLat}&longitude=${useLng}&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code&timezone=auto`;

    const weatherRes = await fetch(weatherUrl);
    if (!weatherRes.ok) throw new Error("Weather API failed");

    const weatherData = await weatherRes.json();
    const current = weatherData.current;

    const condition = wmoToCondition(current.weather_code);

    // Generate a natural language reply
    const reply = generateReply(
      message,
      useLocationName,
      current.temperature_2m,
      condition,
      current.relative_humidity_2m,
      current.wind_speed_10m,
      current.apparent_temperature
    );

    return NextResponse.json({
      reply,
      weather_data: {
        temperature: current.temperature_2m,
        condition,
        humidity: current.relative_humidity_2m,
        wind_speed: `${current.wind_speed_10m} km/h`,
        feels_like: current.apparent_temperature,
        location: useLocationName,
      },
    });
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "WEATHER_API_TIMEOUT", message: "Weather service took too long to respond. Please try again." } } },
      { status: 504 }
    );
  }
}

function generateReply(
  question: string,
  location: string,
  temp: number,
  condition: string,
  humidity: number,
  windSpeed: number,
  feelsLike: number
): string {
  const lower = question.toLowerCase();

  if (lower.includes("rain") || lower.includes("umbrella")) {
    const rainNote =
      condition.toLowerCase().includes("rain") || condition.toLowerCase().includes("drizzle")
        ? `Yes, it looks like rain in ${location} right now — ${condition.toLowerCase()}. You might want an umbrella!`
        : `Currently it's ${condition.toLowerCase()} in ${location} with ${humidity}% humidity. No rain at the moment.`;
    return rainNote;
  }

  if (lower.includes("hot") || lower.includes("warm") || lower.includes("heat")) {
    return `It's currently ${temp}°C in ${location} (feels like ${feelsLike}°C). ${
      temp > 35 ? "It's quite hot — stay hydrated!" : temp > 28 ? "It's warm out there." : "It's not too hot right now."
    }`;
  }

  if (lower.includes("cold") || lower.includes("cool")) {
    return `The temperature in ${location} is ${temp}°C (feels like ${feelsLike}°C). ${
      temp < 10 ? "Bundle up, it's cold!" : temp < 20 ? "It's cool outside." : "It's actually fairly mild."
    }`;
  }

  if (lower.includes("wind")) {
    return `Wind speed in ${location} is ${windSpeed} km/h right now. ${
      windSpeed > 40 ? "It's quite windy!" : windSpeed > 20 ? "There's a moderate breeze." : "Winds are calm."
    }`;
  }

  if (lower.includes("humidity")) {
    return `Humidity in ${location} is currently ${humidity}%. ${
      humidity > 80 ? "It's quite humid." : humidity > 50 ? "Moderate humidity levels." : "The air is relatively dry."
    }`;
  }

  // Generic weather reply
  return `Right now in ${location}, it's ${temp}°C with ${condition.toLowerCase()}. Humidity is ${humidity}% and wind speed is ${windSpeed} km/h. It feels like ${feelsLike}°C.`;
}

function extractLocationFromMessage(message: string): string | null {
  // Match common patterns like "weather in <place>", "in <place>", "for <place>", "at <place>"
  const patterns = [
    /(?:weather|forecast|temperature|rain|conditions?)\s+(?:in|at|for|of)\s+([A-Z][a-zA-Z\s]+)/i,
    /(?:in|at|for)\s+([A-Z][a-zA-Z\s]{2,}?)(?:\?|$|,|\.|!)/i,
    /(?:how(?:'s| is) (?:it |the weather )?in\s+)([A-Z][a-zA-Z\s]+)/i,
  ];

  for (const pattern of patterns) {
    const match = message.match(pattern);
    if (match?.[1]) {
      return match[1].trim();
    }
  }
  return null;
}

function wmoToCondition(code: number): string {
  const map: Record<number, string> = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm",
  };
  return map[code] ?? "Unknown";
}
