import type { Agent, Board, MenuItem } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    credentials: "same-origin",
  });
  if (res.status === 403) throw new Error("forbidden");
  if (!res.ok) {
    let err: { error?: string } = {};
    try {
      err = await res.json();
    } catch {
      /* ignore */
    }
    throw new Error(err.error || `http ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  login: (password: string) =>
    req<{ ok: boolean }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () => req<{ ok: boolean }>("/api/logout", { method: "POST" }),
  board: () => req<Board>("/api/board"),
  agent: (pane: string) =>
    req<Agent>(`/api/agent?pane=${encodeURIComponent(pane)}`),
  prompt: (pane: string, text: string) =>
    req<{ ok: boolean }>("/api/prompt", {
      method: "POST",
      body: JSON.stringify({ pane, text }),
    }),
  answer: (pane: string, answer: unknown) =>
    req<{ ok: boolean }>("/api/answer", {
      method: "POST",
      body: JSON.stringify({ pane, answer }),
    }),
  keys: (pane: string, keys: string[]) =>
    req<{ ok: boolean }>("/api/keys", {
      method: "POST",
      body: JSON.stringify({ pane, keys }),
    }),
  focus: (pane: string) =>
    req<{ ok: boolean }>("/api/focus", {
      method: "POST",
      body: JSON.stringify({ pane }),
    }),
  type: (pane: string, text: string) =>
    req<{ ok: boolean }>("/api/type", {
      method: "POST",
      body: JSON.stringify({ pane, text }),
    }),
  menu: (pane: string) =>
    req<{ items: MenuItem[]; kind: string }>("/api/menu", {
      method: "POST",
      body: JSON.stringify({ pane }),
    }),
  folders: (path: string) =>
    req<{
      path: string;
      home: string;
      parent: string | null;
      can_descend: boolean;
      dirs: { name: string; path: string; has_children: boolean }[];
    }>(`/api/folders?path=${encodeURIComponent(path)}`),
  workspaces: () =>
    req<{
      workspaces: {
        id: string;
        label: string;
        tabs: number;
        agents: string[];
      }[];
    }>("/api/workspaces"),
  hire: (body: Record<string, unknown>) =>
    req<{ ok: boolean }>("/api/hire", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rehire: (key: string) =>
    req<{ ok: boolean }>("/api/rehire", {
      method: "POST",
      body: JSON.stringify({ key }),
    }),
  fire: (pane: string) =>
    req<{ ok: boolean; worktree_removed: boolean; worktree_skip: string }>(
      "/api/fire",
      {
        method: "POST",
        body: JSON.stringify({ pane }),
      },
    ),
  dismiss: (key: string) =>
    req<{ ok: boolean }>("/api/dismiss", {
      method: "POST",
      body: JSON.stringify({ key }),
    }),
  remind: (pane: string) =>
    req<{ ok: boolean }>("/api/remind", {
      method: "POST",
      body: JSON.stringify({ pane }),
    }),
  diff: (repo: string, opts: { path?: string; sha?: string }) =>
    req<{ diff: string }>("/api/diff", {
      method: "POST",
      body: JSON.stringify({ repo, ...opts }),
    }),
  removeWorktree: (path: string) =>
    req<{ ok: boolean }>("/api/worktree_remove", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  createWorktree: (body: { repo: string; branch: string; base?: string; label?: string }) =>
    req<{ ok: boolean }>("/api/worktree_create", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  openWorktree: (path: string) =>
    req<{ ok: boolean }>("/api/worktree_open", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  screen: (pane: string) =>
    req<{ lines: string[] }>(`/api/screen?pane=${encodeURIComponent(pane)}`),
};
