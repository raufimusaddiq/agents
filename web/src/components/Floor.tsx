import type { AgentChipData, Board, Ticket } from "../types";
import { RobotAvatar } from "./RobotAvatar";
import { statusLamp } from "./TicketCard";

/**
 * The "Floor": the same tickets as a 2D lane instead of columns. A stage ruler
 * runs across the top; each ticket is a bold card that sits under the stage it
 * reached and slides there when the stage changes. Agents are our robots riding
 * the card. Flat, ink-outlined, in the board's own palette — no canvas, no
 * WebGL, no borrowed art. Motion only when a stage changes.
 */

const STAGE_COLOR: Record<string, string> = {
  "To do": "var(--ab-col-idle)",
  "In progress": "var(--ab-col-go)",
  Testing: "var(--ab-col-test)",
  Review: "var(--ab-col-review)",
  "Ready to push": "var(--ab-col-ready)",
  Shipped: "var(--ab-col-ship)",
};

function Lane({ ticket, onOpen }: { ticket: Ticket; onOpen: (pane: string) => void }) {
  const needs = ticket.needs_you_count > 0;
  return (
    <div
      className="lane-ticket ab-card"
      data-needs={needs}
      data-testid="lane-ticket"
      style={{ borderLeftColor: STAGE_COLOR[ticket.stage] || "var(--ab-col-idle)" }}
      aria-label={`ticket ${ticket.name}`}
    >
      <div className="lane-head">
        <span className="lane-name font-display">{ticket.name}</span>
        {needs && <span className="lane-flag">needs you</span>}
      </div>
      <div className="lane-meta mono">
        {ticket.repo ? ticket.repo.split("/").pop() : ""}
        {ticket.branch ? ` · ${ticket.branch}` : ""}
      </div>
      <div className="lane-agents">
        {ticket.agents.map((a: AgentChipData) => {
          const lamp = statusLamp(a.agent_status, a.needs_user);
          return (
            <button
              key={a.pane}
              type="button"
              className="lane-agent"
              onClick={() => onOpen(a.pane)}
              aria-label={`agent ${a.name}`}
              title={`${a.name} · ${lamp.label}`}
            >
              <RobotAvatar name={a.name} kind={a.kind} status={a.agent_status} size={24} />
              <span className="lane-agent-name">{a.name}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function FloorView({
  board,
  onSelect,
}: {
  board: Board;
  onSelect: (pane: string | null) => void;
}) {
  const stations = board.stages;
  const n = stations.length;
  const col = (i: number) => `${(i / n) * 100}%`;
  const width = `${100 / n}%`;
  const indexOf = (stage: string) => Math.max(0, stations.indexOf(stage));

  return (
    <div className="floor" aria-label="shop floor">
      {/* stage ruler */}
      <div className="floor-ruler">
        {stations.map((s, i) => (
          <div key={s} className="ruler-cell" style={{ left: col(i), width }}>
            <span className="ruler-dot" style={{ background: STAGE_COLOR[s] }} aria-hidden />
            <span className="ruler-name">{s}</span>
          </div>
        ))}
      </div>

      {/* ticket lanes: one row per ticket, positioned under its stage */}
      <div className="floor-lanes">
        {stations.map((_, i) => (
          <div key={i} className="lane-grid" style={{ left: col(i), width }} aria-hidden />
        ))}
        {board.tickets.map((t) => (
          <div
            key={t.id}
            className="lane-row"
            style={{ left: col(indexOf(t.stage)), width }}
          >
            <Lane ticket={t} onOpen={(p) => onSelect(p)} />
          </div>
        ))}
        {board.tickets.length === 0 && (
          <p className="floor-empty">Nothing on the floor. Hire an agent to start.</p>
        )}
      </div>
    </div>
  );
}
