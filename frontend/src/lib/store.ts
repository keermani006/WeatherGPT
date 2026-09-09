/* ──────────────────────────────────────────────
 * Location Store (Zustand)
 * Shared session state for the active location.
 * Consumed by dashboard, chat, alerts, climate.
 * ────────────────────────────────────────────── */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface LocationState {
  lat: number | null;
  lng: number | null;
  name: string | null;
  setLocation: (lat: number, lng: number, name: string) => void;
  clearLocation: () => void;
}

export const useLocationStore = create<LocationState>()(
  persist(
    (set) => ({
      lat: null,
      lng: null,
      name: null,
      setLocation: (lat, lng, name) => set({ lat, lng, name }),
      clearLocation: () => set({ lat: null, lng: null, name: null }),
    }),
    {
      name: "weathergpt_location",
    }
  )
);

