/* GET /api/v1/location/search?query=... */

import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const query = req.nextUrl.searchParams.get("query");

  if (!query || query.trim().length === 0) {
    return NextResponse.json(
      { detail: { error: { code: "MISSING_QUERY", message: "query is required" } } },
      { status: 400 }
    );
  }

  try {
    const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=5&language=en`;

    const res = await fetch(url, { next: { revalidate: 600 } });
    if (!res.ok) throw new Error("Geocoding unavailable");

    const data = await res.json();

    const results = (data.results ?? []).map(
      (r: { name: string; latitude: number; longitude: number; country?: string; admin1?: string }) => ({
        name: r.name,
        lat: r.latitude,
        lng: r.longitude,
        country: r.country ?? undefined,
        state: r.admin1 ?? undefined,
      })
    );

    return NextResponse.json({ results });
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "GEOCODING_SERVICE_UNAVAILABLE", message: "Search is temporarily unavailable" } } },
      { status: 502 }
    );
  }
}
