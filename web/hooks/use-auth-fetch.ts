// Wrapper around fetch() that attaches a Clerk JWT to every request.
// Use this for all /api/* calls from client components.
"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

export function useAuthFetch() {
  const { getToken } = useAuth();

  return useCallback(
    async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const token = await getToken();
      const headers = new Headers(init.headers);
      if (token) headers.set("Authorization", `Bearer ${token}`);
      return fetch(input, { ...init, headers });
    },
    [getToken],
  );
}
