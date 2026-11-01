// HTTP-клиент API: токен single-user из localStorage, тот же origin в проде.

const BASE = process.env.NEXT_PUBLIC_API_BASE || "";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("toklib_token") || "";
}

export function setToken(token: string) {
  localStorage.setItem("toklib_token", token);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(BASE + path, { ...init, headers });
  if (resp.status === 401) {
    throw new ApiError(401, "Неверный токен — задайте его в настройках (иконка ключа)");
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* не-json ошибка */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// Медиа-эндпоинты допускают ?token= (теги <video>/<img> не управляют заголовками)
export function mediaUrl(path: string): string {
  const token = getToken();
  const sep = path.includes("?") ? "&" : "?";
  return `${BASE}${path}${sep}token=${encodeURIComponent(token)}`;
}
