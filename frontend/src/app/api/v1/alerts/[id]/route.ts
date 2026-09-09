/* DELETE /api/v1/alerts/[id] */

import { NextRequest, NextResponse } from "next/server";
import type { StoredAlert } from "../route";

const globalAlerts = globalThis as unknown as { __alerts?: StoredAlert[] };

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const alerts = globalAlerts.__alerts ?? [];
  const idx = alerts.findIndex((a) => a.id === id);

  if (idx === -1) {
    return NextResponse.json(
      { detail: { error: { code: "ALERT_NOT_FOUND", message: "Alert not found" } } },
      { status: 404 }
    );
  }

  alerts.splice(idx, 1);
  return new NextResponse(null, { status: 204 });
}
