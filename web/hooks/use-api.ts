// SWR factory wired to Clerk JWT. One place for auth + error handling —
// every feature hook is a thin wrapper around this.
"use client";

import useSWR, { SWRConfiguration, useSWRConfig } from "swr";
import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

async function doFetch(url: string, token: string | null, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(BASE + url, { ...init, headers });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

/** GET with Clerk JWT + optional SWR refresh. */
export function useApi<T>(path: string | null, config?: SWRConfiguration) {
  const { getToken } = useAuth();
  return useSWR<T>(
    path,
    async (url: string) => doFetch(url, await getToken()),
    { revalidateOnFocus: false, ...config },
  );
}

/** Returns a function for POST/PUT mutations. */
export function useApiMutate() {
  const { getToken } = useAuth();
  const { mutate } = useSWRConfig();

  const call = useCallback(
    async <T,>(path: string, method: "POST" | "PUT" | "DELETE", body?: unknown): Promise<T> => {
      const token = await getToken();
      return doFetch(path, token, {
        method,
        body: body == null ? undefined : JSON.stringify(body),
      });
    },
    [getToken],
  );

  return { call, mutate };
}
