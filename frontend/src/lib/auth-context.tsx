/* ──────────────────────────────────────────────
 * Auth Context Provider — WeatherGPT
 * Manages JWT authentication state, login, register,
 * guest/demo mode, and session persistence.
 * ────────────────────────────────────────────── */

"use client";

import React, { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  loginUser,
  registerUser,
  logoutUser,
  getDemoToken,
  getMe,
  getAuthToken,
  setAuthToken,
  ApiError,
} from "@/lib/api";
import { useLocationStore } from "@/lib/store";
import type { AuthUser } from "@/lib/types";

interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (credentials: { email: string; password: string }) => Promise<void>;
  register: (data: { email: string; password: string; name?: string }) => Promise<void>;
  loginDemo: (email?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const USER_STORAGE_KEY = "weathergpt_user";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Initialize from storage on mount
  useEffect(() => {
    async function initAuth() {
      const storedToken = getAuthToken();
      if (!storedToken) {
        setIsLoading(false);
        return;
      }

      setTokenState(storedToken);

      // Attempt cached user first for immediate UI render
      try {
        const cachedUserStr = localStorage.getItem(USER_STORAGE_KEY);
        if (cachedUserStr) {
          setUser(JSON.parse(cachedUserStr));
        }
      } catch {
        // ignore JSON parse error
      }

      // Verify token validity with backend
      try {
        const me = await getMe();
        setUser(me);
        localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(me));
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          // Token expired or invalid
          setAuthToken(null);
          setTokenState(null);
          setUser(null);
          localStorage.removeItem(USER_STORAGE_KEY);
        }
      } finally {
        setIsLoading(false);
      }
    }

    initAuth();
  }, []);

  const login = async (credentials: { email: string; password: string }) => {
    const res = await loginUser(credentials);
    setTokenState(res.access_token);
    setUser(res.user);
    try {
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(res.user));
    } catch {
      // ignore
    }
  };

  const register = async (data: { email: string; password: string; name?: string }) => {
    const res = await registerUser(data);
    setTokenState(res.access_token);
    setUser(res.user);
    try {
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(res.user));
    } catch {
      // ignore
    }
  };

  const loginDemo = async (email?: string) => {
    const res = await getDemoToken(email ? { email } : undefined);
    setTokenState(res.access_token);
    const demoUser: AuthUser = {
      id: res.user_id,
      email: res.email,
      name: "Demo Explorer",
      role: "authenticated",
    };
    setUser(demoUser);
    try {
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(demoUser));
    } catch {
      // ignore
    }
  };

  const logout = async () => {
    try {
      await logoutUser();
    } finally {
      setAuthToken(null);
      setTokenState(null);
      setUser(null);
      try {
        useLocationStore.getState().clearLocation();
      } catch {
        // ignore
      }
      try {
        localStorage.removeItem(USER_STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        isAuthenticated: !!token && !!user,
        login,
        register,
        loginDemo,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
