const TOKEN = "ipam_token";

export const getToken = () => localStorage.getItem(TOKEN);
export const setToken = (t: string | null) =>
  t ? localStorage.setItem(TOKEN, t) : localStorage.removeItem(TOKEN);

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  if (opts.body) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch("/api" + path, { ...opts, headers });
  if (res.status === 401 && !path.startsWith("/auth/login")) {
    setToken(null);
    window.location.hash = "#/login";
    throw new Error("Не авторизован");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
