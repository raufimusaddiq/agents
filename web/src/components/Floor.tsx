import type { AgentChipData, Board, Ticket } from "../types";
import { statusLamp } from "./TicketCard";

const STAGE_COLOR: Record<string, string> = {
  "To do": "var(--ab-col-idle)",
  "In progress": "var(--ab-col-go)",
  Testing: "var(--ab-col-test)",
  Review: "var(--ab-col-review)",
  "Ready to push": "var(--ab-col-ready)",
  Shipped: "var(--ab-col-ship)",
};

function Cart({
  ticket,
  stations,
  onOpenAgent,
}: {
  ticket: Ticket;
  stations: string[];
  onOpenAgent: (pane: string) => void;
}) {
  const idx = Math.max(0, stations.indexOf(ticket.stage));
  const left = (idx / (stations.length - 1)) * 100;
  return (
    <div
      className="floor-cart"
      style={{ left: `${left}%` }}
      data-needs={ticket.needs_you_count > 0}
      aria-label={`ticket ${ticket.name} at ${ticket.stage}`}
    >
      <div className="cart-body">
        <span className="cart-name font-display">{ticket.name}</span>
        <span className="cart-stage">{ticket.stage}</span>
      </div>
      <div className="cart-agents">
        {ticket.agents.map((a: AgentChipData) => {
          const lamp = statusLamp(a.agent_status, a.needs_user);
          return (
            <button
              key={a.pane}
              type="button"
              className="cart-agent"
              onClick={() => onOpenAgent(a.pane)}
              aria-label={`agent ${a.name}`}
              title={`${a.name} · ${lamp.label}`}
            >
              <span className={`lamp ${lamp.cls}`} aria-hidden />
              <span className="cart-agent-name">{a.name}</span>
            </button>
          );
        })}
      </div>
      <span className="cart-wheel" aria-hidden />
      <span className="cart-wheel cart-wheel-2" aria-hidden />
    </div>
  );
}

/**
 * The shop floor: a CSS-only view of the same tickets. Stations are fixed
 * columns on a conveyor; each ticket is a cart that slides to the station it
 * has reached. Motion happens only when a stage changes (a transform
 * transition), never on a timer, so an idle floor costs nothing. No canvas and
 * no WebGL, so it stays within the board's performance budget.
 */
export function FloorView({
  board,
  onSelect,
}: {
  board: Board;
  onSelect: (pane: string | null) => void;
}) {
  const stations = board.stages;
  const byStage: Record<string, Ticket[]> = {};
  for (const s of stations) byStage[s] = [];
  let overflow = 0;
  for (const t of board.tickets) {
    if (byStage[t.stage]) byStage[t.stage].push(t);
    else overflow++;
  }
  return (
    <div className="floor" aria-label="shop floor">
      <div className="floor-rail" aria-hidden>
        {stations.map((s) => (
          <span key={s} className="rail-tick" style={{ left: `${(stations.indexOf(s) / (stations.length - 1)) * 100}%` }} />
        ))}
      </div>
      <div className="floor-stations" aria-hidden>
        {stations.map((s) => (
          <span key={s} className="floor-station">
            <span
              className="station-sign font-display"
              data-pale={["To do", "Testing"].includes(s)}
              style={{ background: STAGE_COLOR[s] }}
            >
              {s}
            </span>
          </span>
        ))}
      </div>
      <div className="floor-lanes">
        {stations.map((s, i) => (
          <div
            key={s}
            className="floor-lane"
            style={{ left: `${(i / (stations.length - 1)) * 100}%` }}
            aria-hidden
          />
        ))}
        {board.tickets.map((t) => (
          <Cart
            key={t.id}
            ticket={t}
            stations={stations}
            onOpenAgent={onSelect}
          />
        ))}
      </div>
      {board.tickets.length === 0 && (
        <p className="board-empty">The floor is empty. Hire an agent to start.</p>
      )}
      {overflow > 0 && (
        <p className="panel-note">{overflow} on the floor beyond the stages.</p>
      )}
    </div>
  );
}
