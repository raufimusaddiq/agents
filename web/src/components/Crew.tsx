import type { AgentChipData } from "../types";
import { RobotAvatar } from "./RobotAvatar";
import { statusLamp } from "./TicketCard";

/** The crew: every agent on the shift, grouped by runtime state. */
export function Crew({
  agents,
  onOpenAgent,
  onClose,
}: {
  agents: AgentChipData[];
  onOpenAgent: (pane: string) => void;
  onClose: () => void;
}) {
  const groups: {
    key: string;
    title: string;
    test: (a: AgentChipData) => boolean;
  }[] = [
    { key: "needs", title: "Needs you", test: (a) => a.needs_user },
    {
      key: "working",
      title: "On shift",
      test: (a) => a.agent_status === "working" && !a.needs_user,
    },
    {
      key: "idle",
      title: "Standing by",
      test: (a) => !a.needs_user && ["idle", "done"].includes(a.agent_status),
    },
    {
      key: "other",
      title: "Unknown",
      test: (a) =>
        !a.needs_user && !["working", "idle", "done"].includes(a.agent_status),
    },
  ];
  const placed = new Set<string>();
  return (
    <aside className="crew ab-panel" aria-label="crew">
      <header className="crew-head">
        <span className="crew-title">Crew</span>
        <span className="crew-count">{agents.length}</span>
        <button
          type="button"
          className="ab-btn board-mini"
          aria-label="close crew"
          onClick={onClose}
        >
          Close
        </button>
      </header>
      {agents.length === 0 && (
        <p className="board-empty">No agents on the shift.</p>
      )}
      {groups.map((g) => {
        const list = agents.filter((a) => !placed.has(a.pane) && g.test(a));
        list.forEach((a) => placed.add(a.pane));
        if (!list.length) return null;
        return (
          <section key={g.key} className="crew-group" aria-label={g.title}>
            <h2 className="crew-group-title">{g.title}</h2>
            {list.map((a) => {
              const lamp = statusLamp(a.agent_status, a.needs_user);
              return (
                <button
                  key={a.pane}
                  type="button"
                  className="crew-card"
                  data-testid="crew-agent"
                  aria-label={`crew agent ${a.name}`}
                  data-needs={a.needs_user}
                  onClick={() => onOpenAgent(a.pane)}
                >
                  <span className="crew-avatar">
                    <RobotAvatar
                      name={a.name}
                      kind={a.kind}
                      status={a.agent_status}
                      size={34}
                    />
                  </span>
                  <span className="crew-name">{a.name}</span>
                  <span className="crew-kind">{a.kind}</span>
                  <span className="crew-line">{a.last_line}</span>
                  <span className="visually-hidden">{lamp.label}</span>
                </button>
              );
            })}
          </section>
        );
      })}
    </aside>
  );
}
