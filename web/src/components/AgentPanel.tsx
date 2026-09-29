import { useEffect, useRef, useState } from "react";
import { ScrollArea, Tabs, Textarea } from "@mantine/core";
import type { Agent, ChatRow } from "../types";
import { api } from "../api";
import { AnswerCard } from "./AnswerCard";

function ChatThread({ rows }: { rows: ChatRow[] }) {
  return (
    <div className="chat-thread">
      {rows.map((r, i) => {
        if (r._fold) {
          return (
            <p key={i} className="chat-fold mono">
              {r._fold}
              {r.path ? ` ${r.path}` : ""}
              {r.command ? ` $ ${r.command.slice(0, 80)}` : ""}
              {r.subagent_type ? ` [${r.subagent_type}]` : ""}
              {r._count && r._count > 1 ? ` ×${r._count}` : ""}
            </p>
          );
        }
        const isUser = r.kind === "prompt";
        const isAnswer = r.kind === "answer" || r.kind === "question";
        return (
          <div
            key={i}
            className={
              "chat-line " +
              (isAnswer ? "is-question" : isUser ? "is-you" : "is-agent")
            }
          >
            <span className="chat-who">
              {isAnswer ? "question" : isUser ? "you" : "agent"}
            </span>
            <p
              className="chat-text"
              data-testid={`chat-${r.kind}`}
            >
              {r.answer || r.text}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function CodeChanges({ agent }: { agent: Agent }) {
  const [diff, setDiff] = useState<{ title: string; body: string } | null>(null);
  const g = agent.git;
  if (!g) return <p className="panel-empty">No git repository for this agent.</p>;
  return (
    <div className="panel-stack">
      <div className="branch-bar">
        <span className="branch-name mono">
          {g.branch}
          {g.upstream ? ` → ${g.upstream}` : " (no upstream)"}
        </span>
        <span className="branch-badges">
          {g.ahead != null && <span className="park-chip">↑{g.ahead}</span>}
          {g.behind != null && <span className="park-chip">↓{g.behind}</span>}
          {g.unpushed_commits != null && (
            <span className={g.unpushed_commits ? "park-chip is-warn" : "park-chip"}>
              {g.unpushed_commits} unpushed
            </span>
          )}
        </span>
      </div>

      <h3 className="panel-h">Changed files</h3>
      <p className="panel-note">Read-only. The board never writes to your repo.</p>
      <ul className="file-list">
        {g.files.map((f) => (
          <li key={f.path}>
            <button
              type="button"
              className="file-row"
              onClick={() =>
                api
                  .diff(g.repo, { path: f.path })
                  .then((r) => setDiff({ title: f.path, body: r.diff }))
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
                  .then((r) => setDiff({ title: c.short, body: r.diff }))
              }
            >
              <span className="file-path">
                <span className="mono commit-sha">{c.short}</span> {c.subject}
              </span>
              <span className={c.pushed ? "park-chip is-ok" : "park-chip is-warn"}>
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
          <ScrollArea h={280}>
            <pre className="diff-body">{diff.body || "(empty)"}</pre>
          </ScrollArea>
        </div>
      )}
    </div>
  );
}

function Workflow({ agent }: { agent: Agent }) {
  return (
    <div className="panel-stack">
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
              onClick={() => api.remind(a.pane).then(() => undefined)}
            >
              Remind agent
            </button>
            <button
              type="button"
              className="ab-btn board-mini"
              onClick={() => api.dismiss(a.key).then(() => undefined)}
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
          <dd>{g && g.has_remote ? (g.unpushed_commits ?? 0) : "none"}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{String(agent.status?.model ?? "not reported")}</dd>
        </div>
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
  onClose,
}: {
  pane: string | null;
  onClose: () => void;
}) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | null>(null);

  async function load() {
    if (!pane) return;
    try {
      const a = await api.agent(pane);
      setAgent(a);
    } catch {
      setAgent(null);
    }
  }

  useEffect(() => {
    setAgent(null);
    load();
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(load, 4000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pane]);

  if (!pane || !agent) return null;

  return (
    <section
      className="ab-panel agent-panel"
      aria-label={`agent panel ${agent.name}`}
    >
      <header className="panel-head">
        <div className="panel-id">
          <span className="font-display panel-name">{agent.name}</span>
          <span className="panel-kind">{agent.kind}</span>
          <span className="panel-pane mono">{agent.pane}</span>
        </div>
        <div className="panel-actions">
          <button
            type="button"
            className="ab-btn board-mini"
            onClick={() => api.focus(agent.pane)}
          >
            Open terminal
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

      {agent.ask && (
        <div className="panel-ask">
          <AnswerCard pane={agent.pane} ask={agent.ask} onDone={load} />
        </div>
      )}

      <Tabs defaultValue="chat" className="panel-tabs">
        <Tabs.List>
          <Tabs.Tab value="chat">Chat</Tabs.Tab>
          <Tabs.Tab value="code">Code changes</Tabs.Tab>
          <Tabs.Tab value="workflow">Workflow</Tabs.Tab>
          <Tabs.Tab value="safety">Safety</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="chat" pt="sm">
          <ScrollArea h={360}>
            <ChatThread rows={agent.chat} />
          </ScrollArea>
          <div className="composer">
            <Textarea
              style={{ flex: 1 }}
              autosize
              minRows={1}
              maxRows={4}
              placeholder="Message the agent"
              value={msg}
              onChange={(e) => setMsg(e.currentTarget.value)}
              aria-label="message agent"
            />
            <button
              type="button"
              className="ab-btn hire-submit"
              disabled={loading}
              onClick={async () => {
                if (!msg.trim()) return;
                setLoading(true);
                try {
                  await api.prompt(agent.pane, msg);
                  setMsg("");
                  load();
                } finally {
                  setLoading(false);
                }
              }}
            >
              {loading ? "Sending…" : "Send"}
            </button>
          </div>
          {agent.screen_tail && agent.screen_tail.length > 0 && (
            <div className="screen-tail">
              <h3 className="panel-h">Live screen (fallback)</h3>
              <ScrollArea h={150}>
                <pre className="diff-body">
                  {agent.screen_tail.slice(-30).join("\n")}
                </pre>
              </ScrollArea>
            </div>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="code" pt="sm">
          <CodeChanges agent={agent} />
        </Tabs.Panel>
        <Tabs.Panel value="workflow" pt="sm">
          <Workflow agent={agent} />
        </Tabs.Panel>
        <Tabs.Panel value="safety" pt="sm">
          <Safety agent={agent} />
        </Tabs.Panel>
      </Tabs>
    </section>
  );
}
