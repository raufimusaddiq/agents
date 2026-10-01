import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Modal, ScrollArea, Tabs } from "@mantine/core";
import type { Agent } from "../types";
import { api } from "../api";
import { AnswerCard } from "./AnswerCard";
const TerminalModal = lazy(() =>
  import("./TerminalModal").then((module) => ({
    default: module.TerminalModal,
  })),
);
import { Composer } from "./Composer";
import { ChatView } from "./Chat";
import { statusLamp } from "./TicketCard";

/**
 * The live terminal tail. Auto-scrolls to the newest line and stays pinned
 * unless the user scrolls up, so it always shows what the agent is doing right
 * now. Collapsible, because it can be long.
 */
function ScreenTail({ lines, live }: { lines: string[]; live: string }) {
  const [open, setOpen] = useState(true);
  const preRef = useRef<HTMLPreElement | null>(null);
  const pinned = useRef(true);
  const visible = lines.slice(-80);

  useEffect(() => {
    const el = preRef.current;
    if (!el || !open || !pinned.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lines, open]);

  return (
    <div className="screen-tail" data-live={live}>
      <button
        type="button"
        className="screen-tail-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="lamp is-working" aria-hidden />
        Live screen
        <span className="screen-tail-note">
          {live === "working" ? "agent is working" : live}
        </span>
        <span className="screen-tail-caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <pre
          ref={preRef}
          className="screen-tail-body"
          tabIndex={0}
          role="region"
          aria-label="Live terminal feedback"
          onScroll={() => {
            const el = preRef.current;
            if (!el) return;
            pinned.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          {visible.join("\n")}
        </pre>
      )}
    </div>
  );
}

function CodeChanges({ agent }: { agent: Agent }) {
  const [diff, setDiff] = useState<{ title: string; body: string } | null>(
    null,
  );
  const [error, setError] = useState("");
  const g = agent.git;
  if (!g)
    return <p className="panel-empty">No git repository for this agent.</p>;
  return (
    <div className="panel-stack">
      {error && (
        <p className="hire-error" role="alert">
          {error}
        </p>
      )}
      <div className="branch-bar">
        <span className="branch-name mono">
          {g.branch}
          {g.upstream ? ` → ${g.upstream}` : " (no upstream)"}
        </span>
        <span className="branch-badges">
          {g.ahead != null && <span className="park-chip">↑{g.ahead}</span>}
          {g.behind != null && <span className="park-chip">↓{g.behind}</span>}
          {g.unpushed_commits != null && (
            <span
              className={g.unpushed_commits ? "park-chip is-warn" : "park-chip"}
            >
              {g.unpushed_commits} unpushed
            </span>
          )}
        </span>
      </div>

      <h3 className="panel-h">Changed files</h3>
      <p className="panel-note">
        Read-only. The board never writes to your repo.
      </p>
      <ul className="file-list">
        {g.files.map((f) => (
          <li key={f.path}>
            <button
              type="button"
              className="file-row"
              onClick={() =>
                api
                  .diff(g.repo, { path: f.path })
                  .then((r) => {
                    setError("");
                    setDiff({ title: f.path, body: r.diff });
                  })
                  .catch(() =>
                    setError("Unable to load the file diff. Try again."),
                  )
              }
            >
              <span className="file-path mono">
                {f.status ? `${f.status} ` : ""}
                {f.path}
              </span>
              <span className="file-stat">
                +{f.added} −{f.removed}
              </span>
            </button>
          </li>
        ))}
        {g.files.length === 0 && (
          <li className="panel-empty">Clean working tree.</li>
        )}
      </ul>
      {g.files_capped && <p className="panel-note">Capped at 300 files.</p>}

      <h3 className="panel-h">Last 15 commits</h3>
      <ul className="file-list">
        {g.commits.map((c) => (
          <li key={c.sha}>
            <button
              type="button"
              className="file-row"
              onClick={() =>
                api
                  .diff(g.repo, { sha: c.sha })
                  .then((r) => {
                    setError("");
                    setDiff({ title: c.short, body: r.diff });
                  })
                  .catch(() =>
                    setError("Unable to load the commit diff. Try again."),
                  )
              }
            >
              <span className="file-path">
                <span className="mono commit-sha">{c.short}</span> {c.subject}
              </span>
              <span
                className={c.pushed ? "park-chip is-ok" : "park-chip is-warn"}
              >
                {c.pushed ? "pushed" : "local"}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {diff && (
        <div className="diff-box">
          <div className="diff-head">
            <span className="panel-h">Diff · {diff.title}</span>
            <button
              type="button"
              className="ab-btn board-mini"
              onClick={() => setDiff(null)}
            >
              Close
            </button>
          </div>
          <ScrollArea
            h={280}
            viewportProps={{
              tabIndex: 0,
              role: "region",
              "aria-label": "File diff",
            }}
          >
            <pre className="diff-body">{diff.body || "(empty)"}</pre>
          </ScrollArea>
        </div>
      )}
    </div>
  );
}

function Workflow({ agent, readOnly }: { agent: Agent; readOnly: boolean }) {
  const [error, setError] = useState("");
  return (
    <div className="panel-stack">
      {error && (
        <p className="hire-error" role="alert">
          {error}
        </p>
      )}
      {agent.alerts.length === 0 && (
        <p className="panel-empty">No skipped steps for this agent.</p>
      )}
      {agent.alerts.map((a) => (
        <div key={a.key} className="alert-card">
          <p>{a.message}</p>
          <div className="alert-actions">
            <button
              type="button"
              className="ab-btn board-mini"
              disabled={readOnly}
              onClick={() =>
                api
                  .remind(a.pane)
                  .then(() => setError(""))
                  .catch(() =>
                    setError("Unable to remind this agent. Try again."),
                  )
              }
            >
              Remind agent
            </button>
            <button
              type="button"
              className="ab-btn board-mini"
              disabled={readOnly}
              onClick={() =>
                api
                  .dismiss(a.key)
                  .then(() => setError(""))
                  .catch(() =>
                    setError("Unable to dismiss the alert. Try again."),
                  )
              }
            >
              Dismiss
            </button>
          </div>
        </div>
      ))}
      <h3 className="panel-h">What this harness reports</h3>
      <ul className="cap-list">
        {agent.notes.map((n) => (
          <li key={n} className="cap-note">
            {n}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** What an agent asking to push would send: the commits and the full diff.
 * The board never pushes; this is a review of what would go out. */
function PushReview({ pane }: { pane: string }) {
  const [info, setInfo] = useState<Awaited<
    ReturnType<typeof api.pushinfo>
  > | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    api
      .pushinfo(pane)
      .then((r) => live && setInfo(r))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [pane]);
  if (!info) return <p className="panel-empty">Loading…</p>;
  if (!info.ok) {
    return (
      <p className="panel-empty">
        {info.error || "Nothing to review."}
      </p>
    );
  }
  const n = info.commits.length;
  return (
    <div className="panel-stack">
      <div className="branch-bar">
        <span className="branch-name mono">
          {info.branch}
          {info.upstream ? ` → ${info.upstream}` : " (no upstream)"}
        </span>
        <span className="branch-badges">
          <span className={n ? "park-chip is-warn" : "park-chip"}>
            {n} commit{n === 1 ? "" : "s"} to send
          </span>
        </span>
      </div>
      {info.uncommitted > 0 && (
        <p className="panel-note">
          {info.uncommitted} uncommitted file
          {info.uncommitted === 1 ? "" : "s"} would stay behind.
        </p>
      )}
      {n === 0 && !info.uncommitted && (
        <p className="panel-empty">Nothing to push. Working tree is clean.</p>
      )}
      <ul className="file-list">
        {info.commits.map((c) => (
          <li key={c.short} className="file-row">
            <span className="file-path">
              <span className="mono commit-sha">{c.short}</span> {c.subject}
            </span>
          </li>
        ))}
      </ul>
      {info.stat && <pre className="diff-body">{info.stat}</pre>}
      {(info.diff || info.stat) && (
        <div>
          <button
            type="button"
            className="ab-btn board-mini"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide diff" : "Show full diff"}
          </button>
          {open && (
            <ScrollArea h={320} mt="xs">
              <pre className="diff-body">{info.diff || "(empty)"}</pre>
            </ScrollArea>
          )}
        </div>
      )}
      {info.truncated && (
        <p className="panel-note">Diff truncated for size.</p>
      )}
    </div>
  );
}

function Safety({ agent }: { agent: Agent }) {
  const g = agent.git;
  const status = agent.status as Record<string, unknown>;
  return (
    <div className="panel-stack">
      <h3 className="panel-h">Safe to restart?</h3>
      <dl className="safety-list">
        <div>
          <dt>Uncommitted files</dt>
          <dd>{g ? g.files.length : "no repo"}</dd>
        </div>
        <div>
          <dt>Commits on no remote</dt>
          <dd>
            {g
              ? g.has_remote
                ? (g.unpushed_commits ?? "unknown")
                : "no remote configured"
              : "no repo"}
          </dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{String(agent.status?.model ?? "not reported")}</dd>
        </div>
        {status.tokens_used != null && (
          <div>
            <dt>Tokens used</dt>
            <dd>{Number(status.tokens_used).toLocaleString()}</dd>
          </div>
        )}
        {status.tokens_input != null && (
          <div>
            <dt>Input tokens</dt>
            <dd>{Number(status.tokens_input).toLocaleString()}</dd>
          </div>
        )}
        {status.tokens_output != null && (
          <div>
            <dt>Output tokens</dt>
            <dd>{Number(status.tokens_output).toLocaleString()}</dd>
          </div>
        )}
        {status.cost != null && (
          <div>
            <dt>Session cost</dt>
            <dd>${Number(status.cost).toFixed(4)}</dd>
          </div>
        )}
        <div>
          <dt>Context used</dt>
          <dd>
            {status.context_pct != null
              ? `${status.context_pct}%`
              : `not reported by ${agent.kind}`}
          </dd>
        </div>
      </dl>

      {agent.usage_history.length > 1 && (
        <div>
          <h3 className="panel-h">5-hour usage, last 45 minutes</h3>
          <div className="trend" aria-hidden>
            {agent.usage_history.map(([, pct], i) => (
              <span
                key={i}
                className="trend-bar"
                style={{ height: `${Math.max(3, (pct / 100) * 44)}px` }}
              />
            ))}
          </div>
        </div>
      )}

      <h3 className="panel-h">Harness capabilities</h3>
      <ul className="cap-list">
        {Object.entries(agent.supports).map(([k, v]) => (
          <li key={k} className={v ? "cap-on" : "cap-off"}>
            {k}: {v ? "yes" : `not reported by ${agent.kind}`}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AgentPanel({
  pane,
  readOnly = false,
  onClose,
}: {
  pane: string | null;
  readOnly?: boolean;
  onClose: () => void;
}) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [termOpen, setTermOpen] = useState(false);
  const [fireOpen, setFireOpen] = useState(false);
  const [fireMsg, setFireMsg] = useState("");
  const [fireBusy, setFireBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const mounted = useRef(false);
  const loading = useRef(false);
  const timer = useRef<number | null>(null);

  async function load() {
    if (!pane || loading.current) return;
    loading.current = true;
    try {
      const a = await api.agent(pane);
      if (!mounted.current) return;
      if (a.error) throw new Error(a.error);
      setAgent(a);
      setLoadError("");
    } catch {
      if (mounted.current)
        setLoadError(
          "Unable to refresh this agent. Your draft is kept. Try again.",
        );
    } finally {
      loading.current = false;
    }
  }

  useEffect(() => {
    mounted.current = true;
    setAgent(null);
    load();
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(load, 4000);
    return () => {
      mounted.current = false;
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pane]);

  if (!pane) return null;
  if (!agent)
    return (
      <section className="ab-panel agent-panel" aria-label="agent panel">
        <p role="status">{loadError || "Loading agent…"}</p>
        {loadError && (
          <button className="ab-btn" onClick={() => void load()}>
            Retry
          </button>
        )}
        <button className="ab-btn" onClick={onClose}>
          Close
        </button>
      </section>
    );

  const runtime = statusLamp(
    agent.agent_status || "unknown",
    !!agent.needs_user,
  );
  return (
    <section
      className="ab-panel agent-panel"
      aria-label={`agent panel ${agent.name}`}
    >
      {loadError && (
        <p role="alert">
          {loadError}{" "}
          <button className="ab-btn" onClick={() => void load()}>
            Retry
          </button>
        </p>
      )}
      {fireMsg && !fireOpen && <p role="status">{fireMsg}</p>}
      <header className="panel-head">
        <div className="panel-id">
          <span className="panel-name">{agent.name}</span>
          <span className="panel-kind">{agent.kind}</span>
          <span className="panel-pane mono">{agent.pane}</span>
          <span className="panel-runtime">
            <span className={`lamp ${runtime.cls}`} aria-hidden />
            {runtime.label}
          </span>
        </div>
        <div className="panel-actions">
          <button
            type="button"
            className="ab-btn board-mini"
            disabled={readOnly}
            onClick={() => setTermOpen(true)}
          >
            Open terminal
          </button>
          <button
            type="button"
            className="ab-btn board-mini is-danger"
            disabled={readOnly}
            onClick={() => setFireOpen(true)}
          >
            Fire agent
          </button>
          <button
            type="button"
            className="ab-btn board-mini"
            aria-label="close agent panel"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </header>

      <Modal
        opened={fireOpen}
        onClose={() => setFireOpen(false)}
        title="Fire agent"
        centered
      >
        <p>
          Close {agent.name}&apos;s pane? This stops the {agent.kind} process.
          The agent stays rehirable for 24 hours.
        </p>
        <p className="panel-note">
          If this agent owns a worktree, it is removed too — unless it has
          uncommitted or unpushed work, which is never discarded.
        </p>
        {fireMsg && <p className="hire-error">{fireMsg}</p>}
        <div className="alert-actions">
          <button
            type="button"
            className="ab-btn board-mini"
            onClick={() => setFireOpen(false)}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ab-btn board-mini is-danger"
            disabled={fireBusy}
            onClick={async () => {
              if (fireBusy) return;
              setFireBusy(true);
              setFireMsg("");
              try {
                const result = await api.fire(agent.pane);
                if (result.worktree_skip) {
                  setFireMsg(
                    `Agent stopped. Worktree kept: ${result.worktree_skip}`,
                  );
                } else {
                  setFireOpen(false);
                  onClose();
                }
              } catch (e) {
                setFireMsg(
                  e instanceof Error ? e.message : "Unable to stop the agent.",
                );
              } finally {
                setFireBusy(false);
              }
            }}
          >
            {fireBusy ? "Stopping…" : "Fire"}
          </button>
        </div>
      </Modal>

      {termOpen && (
        <Suspense fallback={<p role="status">Loading terminal…</p>}>
          <TerminalModal
            pane={agent.pane}
            name={agent.name}
            opened={termOpen}
            onClose={() => setTermOpen(false)}
          />
        </Suspense>
      )}

      {agent.ask && (
        <fieldset className="panel-ask" disabled={readOnly}>
          <AnswerCard
            key={JSON.stringify([
              agent.pane,
              agent.ask.question,
              agent.ask.kind,
              agent.ask.multi,
              agent.ask.tabs,
              agent.ask.options.map(
                ({ number, label, description, kind, preview }) => [
                  number,
                  label,
                  description,
                  kind,
                  preview,
                ],
              ),
            ])}
            pane={agent.pane}
            ask={agent.ask}
            onDone={load}
          />
        </fieldset>
      )}

      <Tabs defaultValue="chat" className="panel-tabs">
        <Tabs.List>
          <Tabs.Tab value="chat">Chat</Tabs.Tab>
          <Tabs.Tab value="push">Push</Tabs.Tab>
          <Tabs.Tab value="code">Code changes</Tabs.Tab>
          <Tabs.Tab value="workflow">Workflow</Tabs.Tab>
          <Tabs.Tab value="safety">Safety</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="chat" pt="sm" className="panel-chat">
          <ChatView
            pane={agent.pane}
            label={agent.name}
            status={agent.agent_status || "unknown"}
          />
          <Composer
            key={agent.pane}
            pane={agent.pane}
            label={agent.name}
            cwd={agent.cwd}
            onSend={load}
            readOnly={readOnly}
          />
          {agent.screen_tail && agent.screen_tail.length > 0 && (
            <ScreenTail
              lines={agent.screen_tail}
              live={agent.agent_status || "unknown"}
            />
          )}
        </Tabs.Panel>

        <Tabs.Panel value="push" pt="sm">
          <PushReview pane={agent.pane} />
        </Tabs.Panel>
        <Tabs.Panel value="code" pt="sm">
          <CodeChanges agent={agent} />
        </Tabs.Panel>
        <Tabs.Panel value="workflow" pt="sm">
          <Workflow agent={agent} readOnly={readOnly} />
        </Tabs.Panel>
        <Tabs.Panel value="safety" pt="sm">
          <Safety agent={agent} />
        </Tabs.Panel>
      </Tabs>
    </section>
  );
}
