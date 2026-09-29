import { useEffect, useState } from "react";
import {
  Modal,
  ScrollArea,
  Select,
  Stack,
  TextInput,
} from "@mantine/core";
import type { Board, Card as CardT, FolderListing, Worktree } from "../types";
import { api } from "../api";
import { CardView } from "./CardView";
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!opened) return;
    api
      .workspaces()
      .then((r) => setWorkspaces(r.workspaces))
      .catch(() => setWorkspaces([]));
  }, [opened]);

  async function submit() {
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
      title={<span className="font-display hire-title">Hire an agent</span>}
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
            Adds a tab to{" "}
            <strong>{sameWorkspace?.label || workspace}</strong>
            {sameWorkspace && sameWorkspace.agents.length > 0
              ? ` — already there: ${sameWorkspace.agents.join(", ")}`
              : ""}
          </p>
        )}
        <FolderPicker
          value={preset?.folder ?? folder}
          onChange={setFolder}
        />
        <TextInput
          label="Name (1-40 chars)"
          value={preset?.name ?? name}
          onChange={(e) => setName(e.currentTarget.value)}
          aria-label="hire name"
        />
        <TextInput
          label="Optional first message"
          value={message}
          onChange={(e) => setMessage(e.currentTarget.value)}
        />
        {error && <p className="hire-error">{error}</p>}
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

function WorktreeCards({
  worktrees,
  onRehire,
}: {
  worktrees: Worktree[];
  onRehire: (w: Worktree) => void;
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
          <p className="park-branch font-display">{w.branch || "detached"}</p>
          <p className="park-path mono">{w.path}</p>
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
              onClick={() => onRehire(w)}
            >
              Rehire here
            </button>
            <button
              type="button"
              className="ab-btn board-mini is-danger"
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
          {err && <p className="hire-error">Remove refused: {err}</p>}
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
  theme,
  onToggleTheme,
  selected,
  onSelect,
}: {
  board: Board;
  theme: string;
  onToggleTheme: () => void;
  selected: string | null;
  onSelect: (pane: string | null) => void;
}) {
  const [ticket, setTicket] = useState<string | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [onlyNeeds, setOnlyNeeds] = useState(false);
  const [hireOpen, setHireOpen] = useState(false);
  const [hirePreset, setHirePreset] = useState<{ folder: string; name: string } | null>(null);
  const ref = useFlip(JSON.stringify(board.cards.map((c) => [c.pane, c.column])));

  const ticketNames = Array.from(new Set(board.cards.map((c) => c.ticket))).sort();
  let cards = board.cards;
  if (ticket) cards = cards.filter((c) => c.ticket === ticket);
  if (kind) cards = cards.filter((c) => c.kind === kind);
  if (onlyNeeds) cards = cards.filter((c) => c.needs_user);
  const needsCount = board.cards.filter((c) => c.needs_user).length;

  return (
    <div className="board-shell">
      <header className="ab-panel board-header">
        <div className="board-brand">
          <span className="font-display board-wordmark">Agent&nbsp;Board</span>
        </div>

        <div
          className={needsCount ? "ab-marquee board-marquee is-live" : "board-marquee"}
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
            placeholder="ticket"
            clearable
            data={ticketNames}
            value={ticket}
            onChange={setTicket}
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
          <h2 className="font-display board-alerts-title">
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
                  onClick={() => api.remind(a.pane)}
                >
                  Remind agent
                </button>
                <button
                  type="button"
                  className="ab-btn board-mini"
                  onClick={() => api.dismiss(a.key)}
                >
                  Dismiss
                </button>
              </span>
            </div>
          ))}
        </section>
      )}

      <div ref={ref} className="board-columns">
        {board.columns.map((col) => {
          const colCards = cards.filter((c) => c.column === col);
          const count = col === "Parked" ? board.worktrees.length : colCards.length;
          return (
            <section
              key={col}
              className="board-col"
              data-col={col.toLowerCase().replace(" ", "-")}
              aria-label={`column ${col}`}
            >
              <header className="board-col-head">
                <h2 className="font-display board-col-title">{col}</h2>
                <span className="board-col-count" aria-hidden>
                  {count}
                </span>
              </header>
              <ScrollArea h="calc(100vh - 280px)" type="hover">
                <Stack gap="xs" pt={6}>
                  {col === "Parked" ? (
                    <WorktreeCards
                      worktrees={board.worktrees}
                      onRehire={(w) => {
                        setHirePreset({
                          folder: w.path,
                          name:
                            w.branch
                              .replace(/^refs\/heads\//, "")
                              .split("/")
                              .pop() || "agent",
                        });
                        setHireOpen(true);
                      }}
                    />
                  ) : (
                    colCards.map((c: CardT) => (
                      <CardView
                        key={c.pane}
                        card={c}
                        selected={selected === c.pane}
                        onOpen={() => onSelect(c.pane)}
                      />
                    ))
                  )}
                </Stack>
              </ScrollArea>
            </section>
          );
        })}
      </div>

      {board.closed.length > 0 && (
        <footer className="board-closed ab-panel">
          <span className="board-closed-label">
            Closed, rehirable for 24 hours
          </span>
          <span className="board-closed-list">
            {board.closed.map((c) => (
              <button
                key={c.pane}
                type="button"
                className="ab-btn board-mini"
                onClick={() => api.rehire(c.pane)}
              >
                Rehire {c.name || c.pane}
              </button>
            ))}
          </span>
        </footer>
      )}

      <HireModal
        opened={hireOpen}
        onClose={() => setHireOpen(false)}
        onDone={() => undefined}
        preset={hirePreset}
      />
    </div>
  );
}
