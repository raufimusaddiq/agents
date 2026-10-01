import { useEffect, useRef, useState } from "react";
import { Modal, ScrollArea, Select, Stack, TextInput } from "@mantine/core";
import type { Board, FolderListing, Worktree } from "../types";
import { api } from "../api";
import { TicketCard } from "./TicketCard";
import { Crew } from "./Crew";
import { useFlip } from "../flip";

function FolderPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (path: string) => void;
}) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [open, setOpen] = useState(false);

  async function browse(path: string) {
    try {
      const r = await api.folders(path);
      setListing(r);
      setOpen(true);
    } catch {
      setListing(null);
    }
  }

  return (
    <div className="folder-picker">
      <label className="folder-label" htmlFor="hire-folder">
        Folder (under $HOME)
      </label>
      <div className="folder-row">
        <input
          id="hire-folder"
          className="folder-input"
          value={value}
          onChange={(e) => onChange(e.currentTarget.value)}
          aria-label="hire folder"
        />
        <button
          type="button"
          className="ab-btn board-mini"
          aria-label="browse folders"
          onClick={() => browse(value || "~")}
        >
          Browse
        </button>
      </div>
      {open && listing && (
        <div className="folder-browser">
          <div className="folder-cur mono" title={listing.path}>
            {listing.path}
          </div>
          <ScrollArea h={180}>
            <ul className="folder-list">
              {listing.parent && (
                <li>
                  <button
                    type="button"
                    className="folder-item is-up"
                    onClick={() => browse(listing.parent!)}
                  >
                    ↑ ..
                  </button>
                </li>
              )}
              {listing.dirs.map((d) => (
                <li key={d.path}>
                  <button
                    type="button"
                    className="folder-item"
                    onClick={() => {
                      onChange(d.path);
                      if (d.has_children && listing.can_descend) {
                        browse(d.path);
                      } else {
                        setOpen(false);
                      }
                    }}
                    aria-label={`select ${d.name}`}
                  >
                    <span className="folder-name">{d.name}</span>
                    {d.has_children && listing.can_descend && (
                      <span className="folder-enter">›</span>
                    )}
                  </button>
                </li>
              ))}
              {listing.dirs.length === 0 && (
                <li className="panel-empty">No subfolders here.</li>
              )}
            </ul>
          </ScrollArea>
          <button
            type="button"
            className="ab-btn board-mini folder-use"
            onClick={() => setOpen(false)}
          >
            Use this folder
          </button>
        </div>
      )}
    </div>
  );
}

function HireModal({
  opened,
  onClose,
  onDone,
  preset,
}: {
  opened: boolean;
  onClose: () => void;
  onDone: () => void;
  preset?: { folder: string; name: string } | null;
}) {
  const [kind, setKind] = useState("claude");
  const [workspace, setWorkspace] = useState("new");
  const [workspaceLabel, setWorkspaceLabel] = useState("");
  const [workspaces, setWorkspaces] = useState<
    { id: string; label: string; tabs: number; agents: string[] }[]
  >([]);
  const [folder, setFolder] = useState("~");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [useWorktree, setUseWorktree] = useState(false);
  const [worktreeBranch, setWorktreeBranch] = useState("");
  const [yolo, setYolo] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!opened) return;
    if (preset) {
      setFolder(preset.folder);
      setName(preset.name);
    }
    setError("");
    api
      .workspaces()
      .then((r) => setWorkspaces(r.workspaces))
      .catch(() => setWorkspaces([]));
  }, [opened, preset]);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.hire({
        kind,
        workspace,
        workspace_label: workspaceLabel,
        folder,
        name,
        message,
        use_worktree: useWorktree,
        worktree_branch: worktreeBranch,
        yolo,
      });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  const sameWorkspace = workspaces.find((w) => w.id === workspace);
  const workspaceData = [
    { value: "new", label: "New workspace" },
    ...workspaces.map((w) => ({
      value: w.id,
      label: `${w.label || w.id} · ${w.tabs} tab${w.tabs === 1 ? "" : "s"}`,
    })),
  ];

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={<span className="hire-title">Hire an agent</span>}
      centered
    >
      <Stack gap="sm">
        <Select
          label="Harness"
          data={["claude", "codex", "opencode"]}
          value={kind}
          onChange={(v) => setKind(v || "claude")}
          allowDeselect={false}
        />
        <Select
          label="Workspace"
          description="A workspace holds one or more agents, one per tab."
          data={workspaceData}
          value={workspace}
          onChange={(v) => setWorkspace(v || "new")}
          allowDeselect={false}
        />
        {workspace === "new" ? (
          <TextInput
            label="Workspace name"
            placeholder="e.g. acme-checkout"
            value={workspaceLabel}
            onChange={(e) => setWorkspaceLabel(e.currentTarget.value)}
            aria-label="workspace name"
          />
        ) : (
          <p className="panel-note">
            Adds a tab to <strong>{sameWorkspace?.label || workspace}</strong>
            {sameWorkspace && sameWorkspace.agents.length > 0
              ? ` — already there: ${sameWorkspace.agents.join(", ")}`
              : ""}
          </p>
        )}
        <FolderPicker value={folder} onChange={setFolder} />
        <TextInput
          label="Name (1–32 lowercase characters)"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          aria-label="hire name"
        />
        <TextInput
          label="Optional first message"
          value={message}
          onChange={(e) => setMessage(e.currentTarget.value)}
        />
        <div className="hire-opts">
          <label className="hire-opt">
            <input
              type="checkbox"
              checked={useWorktree}
              onChange={(e) => setUseWorktree(e.currentTarget.checked)}
              aria-label="use worktree"
            />
            <span>
              Own worktree
              <span className="hire-opt-note">
                {" "}
                isolated checkout on a new branch. Skip it when the repo has
                just this one agent.
              </span>
            </span>
          </label>
          {useWorktree && (
            <TextInput
              label="Worktree branch (optional)"
              placeholder="defaults to agent/<name>"
              value={worktreeBranch}
              onChange={(e) => setWorktreeBranch(e.currentTarget.value)}
              aria-label="worktree branch"
            />
          )}
          <label className="hire-opt">
            <input
              type="checkbox"
              checked={yolo}
              onChange={(e) => setYolo(e.currentTarget.checked)}
              aria-label="yolo mode"
            />
            <span>
              YOLO mode
              <span className="hire-opt-note">
                {" "}
                skip permission prompts. The agent can run anything.
              </span>
            </span>
          </label>
        </div>
        {error && (
          <p className="hire-error" role="alert">
            {error}
          </p>
        )}
        <button
          type="button"
          className="ab-btn hire-submit"
          onClick={submit}
          disabled={busy}
          data-testid="hire-submit"
        >
          {busy ? "Starting…" : "Start agent"}
        </button>
      </Stack>
    </Modal>
  );
}

/** While you were away: after a while off the page, a per-agent digest of what
 * changed. Anything waiting on the user goes first. Collapsed to one line. */
function DigestStrip() {
  const [rows, setRows] = useState<
    Awaited<ReturnType<typeof api.digest>>["agents"] | null
  >(null);
  const [open, setOpen] = useState(false);
  const away = useRef(Date.now());
  useEffect(() => {
    const onVis = async () => {
      if (document.visibilityState !== "visible") {
        away.current = Date.now();
        return;
      }
      // Only bother after a real absence.
      if (Date.now() - away.current < 30 * 60 * 1000) return;
      try {
        const r = await api.digest(away.current / 1000);
        const changed = r.agents.filter(
          (a) => a.prompts || a.replies || a.commits.length || a.needs_user,
        );
        if (changed.length) setRows(changed);
      } catch {
        /* ignore */
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);
  if (!rows || rows.length === 0) return null;
  const waiting = rows.filter((r) => r.needs_user).length;
  return (
    <section
      className="ab-panel board-digest"
      aria-label="while you were away"
    >
      <button
        type="button"
        className="board-digest-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="board-digest-label">
          While you were away
          <span className="board-digest-note">
            {" "}
            {rows.length} agent{rows.length === 1 ? "" : "s"}
            {waiting ? `, ${waiting} waiting on you` : ""}
          </span>
        </span>
        <span className="board-closed-caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <ul className="board-digest-list">
          {rows.map((r) => (
            <li key={r.pane} className={r.needs_user ? "is-needs" : ""}>
              <span className="font-display">{r.label}</span>
              <span className="board-digest-stats">
                {r.prompts} prompts · {r.replies} replies · {r.tools} tools
                {r.commits.length ? ` · ${r.commits.length} commits` : ""}
                {r.cost ? ` · $${r.cost.toFixed(2)}` : ""}
              </span>
              {r.asking && (
                <span className="board-digest-ask">waiting: {r.asking}</span>
              )}
              {r.last && !r.asking && (
                <span className="board-digest-last">{r.last}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function WorktreeCards({
  worktrees,
  onRehire,
  onOpen,
  readOnly,
}: {
  readOnly: boolean;
  worktrees: Worktree[];
  onRehire: (w: Worktree) => void;
  onOpen: (w: Worktree) => void;
}) {
  const [err, setErr] = useState("");
  return (
    <Stack gap="xs">
      {worktrees.map((w) => (
        <article
          key={w.path}
          className="ab-card park-card"
          data-flip-id={`wt:${w.path}`}
          aria-label={`worktree ${w.path}`}
        >
          <p className="park-branch">{w.ticket || "Unnamed work"}</p>
          <p className="park-path mono">{w.branch || "detached"}</p>
          <p className="park-stats">
            <span className={w.uncommitted ? "park-chip is-warn" : "park-chip"}>
              {w.uncommitted} uncommitted
            </span>
            <span className="park-chip">{w.unmerged} unmerged</span>
          </p>
          <div className="park-actions">
            <button
              type="button"
              className="ab-btn board-mini"
              disabled={readOnly}
              onClick={() => onRehire(w)}
            >
              Rehire here
            </button>
            <button
              type="button"
              className="ab-btn board-mini"
              disabled={readOnly}
              onClick={() => onOpen(w)}
            >
              Open
            </button>
            <button
              type="button"
              className="ab-btn board-mini is-danger"
              disabled={readOnly}
              onClick={() => {
                setErr("");
                api.removeWorktree(w.path).catch((e) => {
                  setErr(e instanceof Error ? e.message : "failed");
                });
              }}
            >
              Remove
            </button>
          </div>
          {err && (
            <p className="hire-error" role="alert">
              Remove refused: {err}
            </p>
          )}
        </article>
      ))}
      {worktrees.length === 0 && (
        <p className="board-empty">No parked worktrees.</p>
      )}
    </Stack>
  );
}

export function BoardView({
  board,
  connectionState,
  onLogout,
  logoutBusy,
  theme,
  onToggleTheme,
  onSelect,
}: {
  board: Board;
  connectionState: "connecting" | "live" | "reconnecting";
  onLogout: () => void;
  logoutBusy: boolean;
  theme: string;
  onToggleTheme: () => void;
  selected: string | null;
  onSelect: (pane: string | null) => void;
}) {
  const [repo, setRepo] = useState<string | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [onlyNeeds, setOnlyNeeds] = useState(false);
  const [crewOpen, setCrewOpen] = useState(
    typeof window !== "undefined" ? window.innerWidth >= 900 : true,
  );
  const [hireOpen, setHireOpen] = useState(false);
  const [closedOpen, setClosedOpen] = useState(false);
  const [hirePreset, setHirePreset] =
    useState<{ folder: string; name: string } | null>(null);
  const ref = useFlip(
    JSON.stringify(board.tickets.map((t) => [t.id, t.stage])),
  );

  const repos = Array.from(
    new Set(board.tickets.map((t) => t.repo).filter(Boolean) as string[]),
  ).sort();
  const repoName = (r: string) => r.split("/").pop() || r;

  let tickets = board.tickets;
  if (repo) tickets = tickets.filter((t) => t.repo === repo);
  if (kind)
    tickets = tickets.filter((t) => t.agents.some((a) => a.kind === kind));
  if (onlyNeeds) tickets = tickets.filter((t) => t.needs_you_count > 0);
  const needsCount = board.tickets.reduce((n, t) => n + t.needs_you_count, 0);

  return (
    <div className="board-shell">
      <header className="ab-panel board-header">
        <div className="board-brand">
          <span className="font-display board-wordmark">Agent&nbsp;Board</span>
          <span
            className="board-feedback"
            data-live={connectionState === "live"}
            role="status"
          >
            <span
              className={`lamp ${connectionState === "live" ? "is-idle" : "is-unknown"}`}
              aria-hidden
            />
            {connectionState === "live"
              ? "Live"
              : connectionState === "reconnecting"
                ? "Reconnecting…"
                : "Connecting…"}
            <span className="board-feedback-time">
              Updated{" "}
              {new Date(board.now * 1000).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </span>
          </span>
        </div>

        <div
          className={
            needsCount ? "ab-marquee board-marquee is-live" : "board-marquee"
          }
          data-testid="needs-counter"
          role="status"
          aria-live="polite"
        >
          <span className="ar-mark" aria-hidden>
            ▮
          </span>
          <span className="font-display board-marquee-num">{needsCount}</span>
          <span className="board-marquee-label">
            {needsCount === 1 ? "agent needs you" : "agents need you"}
          </span>
        </div>

        <div className="board-controls">
          <Select
            size="xs"
            placeholder="repo"
            clearable
            data={repos.map((r) => ({ value: r, label: repoName(r) }))}
            value={repo}
            onChange={setRepo}
            w={130}
            comboboxProps={{ withinPortal: true }}
          />
          <Select
            size="xs"
            placeholder="harness"
            clearable
            data={["claude", "codex", "opencode"]}
            value={kind}
            onChange={setKind}
            w={120}
            comboboxProps={{ withinPortal: true }}
          />
          <button
            type="button"
            className="ab-btn board-ctl"
            aria-pressed={onlyNeeds}
            data-active={onlyNeeds}
            onClick={() => setOnlyNeeds((v) => !v)}
          >
            Only needs you
          </button>
          <button
            type="button"
            className="ab-btn board-ctl"
            aria-pressed={crewOpen}
            data-active={crewOpen}
            data-testid="crew-toggle"
            onClick={() => setCrewOpen((v) => !v)}
          >
            Crew
          </button>
          <button
            type="button"
            className="ab-btn board-ctl"
            onClick={onLogout}
            disabled={logoutBusy}
          >
            {logoutBusy ? "Signing out…" : "Sign out"}
          </button>
          {board.remote_on && (
            <span className="board-remote" data-testid="remote-badge">
              remote on
            </span>
          )}
          <button
            type="button"
            className="ab-btn board-ctl"
            data-testid="theme-toggle"
            aria-label="toggle theme"
            onClick={onToggleTheme}
          >
            {theme === "dark" ? "Day shift" : "Night shift"}
          </button>
          <button
            type="button"
            className="ab-btn board-ctl is-primary"
            disabled={board.read_only}
            data-testid="hire-open"
            onClick={() => {
              setHirePreset(null);
              setHireOpen(true);
            }}
          >
            Hire an agent
          </button>
        </div>
      </header>

      {board.frozen_roster && (
        <p className="board-banner ab-panel" role="alert">
          Something restarted. The roster is frozen — rehire the agents below.
        </p>
      )}

      {board.alerts.length > 0 && (
        <section className="ab-panel board-alerts" aria-label="workflow alerts">
          <h2 className="board-alerts-title">
            Skipped steps
            <span className="board-alerts-note">last 24 hours</span>
          </h2>
          {board.alerts.slice(0, 5).map((a) => (
            <div key={a.key} className="board-alert">
              <span className="board-alert-msg">{a.message}</span>
              <span className="board-alert-actions">
                <button
                  type="button"
                  className="ab-btn board-mini"
                  disabled={!!board.read_only}
                  onClick={() => api.remind(a.pane)}
                >
                  Remind agent
                </button>
                <button
                  type="button"
                  className="ab-btn board-mini"
                  disabled={!!board.read_only}
                  onClick={() => api.dismiss(a.key)}
                >
                  Dismiss
                </button>
              </span>
            </div>
          ))}
        </section>
      )}

      <DigestStrip />

      {board.closed.length > 0 && (
        <section className="ab-panel board-closed" aria-label="closed agents">
          <button
            type="button"
            className="board-closed-head"
            aria-expanded={closedOpen}
            onClick={() => setClosedOpen((v) => !v)}
          >
            <span className="board-closed-label">
              Off shift
              <span className="board-closed-note">
                {" "}
                {board.closed.length} rehirable for 24 hours
              </span>
            </span>
            <span className="board-closed-caret" aria-hidden>
              {closedOpen ? "▾" : "▸"}
            </span>
          </button>
          {closedOpen && (
            <div className="board-closed-list">
              {board.closed.map((c) => (
                <button
                  key={c.pane}
                  type="button"
                  className="ab-btn board-mini"
                  disabled={!!board.read_only}
                  data-testid="rehire"
                  onClick={() => api.rehire(c.pane)}
                >
                  Rehire {c.name || c.pane}
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="board-body">
        <div
          ref={ref}
          className="board-columns"
          tabIndex={0}
          role="region"
          aria-label="Ticket board"
        >
          {board.columns.map((col) => {
            const colTickets =
              col === "Parked" ? [] : tickets.filter((t) => t.stage === col);
            const count =
              col === "Parked" ? board.worktrees.length : colTickets.length;
            return (
              <section
                key={col}
                className="board-col"
                data-col={col.toLowerCase().replace(/ /g, "-")}
                aria-label={`column ${col}`}
              >
                <header className="board-col-head">
                  <h2 className="font-display board-col-title">{col}</h2>
                  <span className="board-col-count" aria-hidden>
                    {count}
                  </span>
                </header>
                <div className="board-col-scroll">
                  <Stack gap="xs">
                    {col === "Parked" ? (
                      <WorktreeCards
                        worktrees={board.worktrees}
                        readOnly={!!board.read_only}
                        onRehire={(w) => {
                          setHirePreset({
                            folder: w.path,
                            name:
                              (w.ticket || "agent")
                                .replace(/[^a-z0-9_-]/gi, "-")
                                .toLowerCase()
                                .slice(0, 32) || "agent",
                          });
                          setHireOpen(true);
                        }}
                        onOpen={(w) =>
                          api.openWorktree(w.path).catch(() => undefined)
                        }
                      />
                    ) : (
                      colTickets.map((t) => (
                        <TicketCard
                          key={t.id}
                          ticket={t}
                          onOpenAgent={onSelect}
                        />
                      ))
                    )}
                  </Stack>
                </div>
              </section>
            );
          })}
        </div>

        {crewOpen && (
          <Crew
            agents={board.agents}
            onOpenAgent={onSelect}
            onClose={() => setCrewOpen(false)}
          />
        )}
      </div>

      <HireModal
        opened={hireOpen}
        onClose={() => setHireOpen(false)}
        onDone={() => undefined}
        preset={hirePreset}
      />
    </div>
  );
}
