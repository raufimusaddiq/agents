import type { AgentChipData, Ticket } from "../types";

const KIND_COLOR: Record<string, string> = {
  claude: "var(--ab-badge-claude)",
  codex: "var(--ab-badge-codex)",
  opencode: "var(--ab-badge-opencode)",
};

/** A status lamp. Agent runtime state is a light, never a board column. */
export function statusLamp(status: string, needsUser: boolean) {
  if (needsUser) return { cls: "is-needs", label: "needs you" };
  switch (status) {
    case "working":
      return { cls: "is-working", label: "on shift" };
    case "blocked":
      return { cls: "is-needs", label: "blocked" };
    case "idle":
      return { cls: "is-idle", label: "standing by" };
    case "done":
      return { cls: "is-idle", label: "standing by" };
    default:
      return { cls: "is-unknown", label: "unknown" };
  }
}

export function AgentChip({
  agent,
  onOpen,
}: {
  agent: AgentChipData;
  onOpen: () => void;
}) {
  const lamp = statusLamp(agent.agent_status, agent.needs_user);
  return (
    <button
      type="button"
      className="agent-chip"
      onClick={onOpen}
      aria-label={`agent ${agent.name}`}
      data-needs={agent.needs_user}
    >
      <span className={`lamp ${lamp.cls}`} aria-hidden />
      <span className="chip-name">{agent.name}</span>
      <span
        className="chip-kind"
        style={{ color: KIND_COLOR[agent.kind] || "var(--ab-dim)" }}
      >
        {agent.kind}
      </span>
      {agent.context_pct != null && (
        <span className="chip-ctx">{agent.context_pct}%</span>
      )}
      <span className="visually-hidden">{lamp.label}</span>
    </button>
  );
}

export function TicketCard({
  ticket,
  onOpenAgent,
  onOpenWorktree,
}: {
  ticket: Ticket;
  onOpenAgent: (pane: string) => void;
  onOpenWorktree?: () => void;
}) {
  const needs = ticket.needs_you_count > 0;
  return (
    <div
      className="ab-card ticket-card"
      data-testid="ticket"
      data-needs={needs}
      data-stage={ticket.stage.toLowerCase().replace(/ /g, "-")}
      aria-label={`ticket ${ticket.name}`}
    >
      <header className="ticket-head">
        <span className="ticket-name">{ticket.name}</span>
        {needs && <span className="ticket-alarm">needs you</span>}
      </header>
      <p className="ticket-meta">
        <span className="ticket-source">{ticket.source || "work"}</span>
        {ticket.repo && (
          <span className="ticket-repo mono">
            {ticket.repo.split("/").pop()}
            {ticket.branch ? ` · ${ticket.branch}` : ""}
          </span>
        )}
      </p>
      {ticket.worktree && (
        <p className="ticket-wt mono" title={ticket.worktree}>
          ⎇ {ticket.worktree}
        </p>
      )}
      <div className="ticket-agents">
        {ticket.agents.map((a) => (
          <AgentChip
            key={a.pane}
            agent={a}
            onOpen={() => onOpenAgent(a.pane)}
          />
        ))}
      </div>
      {ticket.unpushed != null && ticket.unpushed > 0 && (
        <p className="ticket-unpushed">{ticket.unpushed} unpushed</p>
      )}
      {ticket.context_pct_max != null && (
        <p className="ticket-ctx">ctx {ticket.context_pct_max}% max</p>
      )}
      {onOpenWorktree && (
        <button
          type="button"
          className="ab-btn board-mini"
          onClick={onOpenWorktree}
        >
          Rehire here
        </button>
      )}
    </div>
  );
}
