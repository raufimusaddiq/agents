import type { AgentChipData, Board, Ticket } from "../types";
import { statusLamp } from "./TicketCard";

/**
 * The shop floor: a top-down plan of the room. Each work stage is a bench along
 * the floor; agents are workers standing at the bench where their job is, and
 * they walk to the next bench as the job advances. A job card sits on the bench.
 *
 * Everything is positioned from data (stage index -> x, worker index -> y), so
 * it is a real layout, not a restyled list. Flat SVG/CSS in the board palette;
 * motion only when a stage changes.
 */

const STAGE_TINT: Record<string, string> = {
  "To do": "var(--ab-col-idle)",
  "In progress": "var(--ab-col-go)",
  Testing: "var(--ab-col-test)",
  Review: "var(--ab-col-review)",
  "Ready to push": "var(--ab-col-ready)",
  Shipped: "var(--ab-col-ship)",
};

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

const BODY = [
  "var(--ab-col-test)",
  "var(--ab-col-go)",
  "var(--ab-col-ship)",
  "var(--ab-col-review)",
  "var(--signal-red)",
  "var(--ab-col-park)",
];

/** A worker seen from above: head, two arms, a trolley of parts. */
function Worker({
  agent,
  onOpen,
}: {
  agent: AgentChipData;
  onOpen: () => void;
}) {
  const lamp = statusLamp(agent.agent_status, agent.needs_user);
  const working = agent.agent_status === "working";
  const needs = agent.needs_user;
  const color = BODY[hash(agent.name) % BODY.length];
  return (
    <button
      type="button"
      className={`worker ${working ? "is-working" : ""} ${needs ? "is-needs" : ""}`}
      onClick={onOpen}
      aria-label={`agent ${agent.name}`}
      title={`${agent.name} · ${lamp.label}`}
    >
      <svg viewBox="0 0 48 48" width="44" height="44" aria-hidden>
        {/* ground shadow */}
        <ellipse cx="24" cy="40" rx="15" ry="5" fill="#00000022" />
        {/* trolley of parts */}
        <rect x="4" y="26" width="12" height="9" rx="1.5"
              fill="var(--ab-panel)" stroke="var(--ab-ink)" strokeWidth="1.6" />
        <circle cx="7" cy="36" r="2" fill="var(--ab-ink)" />
        <circle cx="13" cy="36" r="2" fill="var(--ab-ink)" />
        {/* arms, out front; pumping when working */}
        <g className="wk-arm wk-arm-l">
          <rect x="14" y="30" width="7" height="4" rx="2" fill={color} stroke="var(--ab-ink)" strokeWidth="1.4" />
        </g>
        <g className="wk-arm wk-arm-r">
          <rect x="27" y="30" width="7" height="4" rx="2" fill={color} stroke="var(--ab-ink)" strokeWidth="1.4" />
        </g>
        {/* body: shoulders from above, an oval */}
        <ellipse cx="24" cy="24" rx="12" ry="10"
                 fill={color} stroke="var(--ab-ink)" strokeWidth="2" />
        {/* head from above */}
        <circle cx="24" cy="20" r="7" fill="var(--ab-panel)" stroke="var(--ab-ink)" strokeWidth="2" />
        {/* cap visor, pointing "forward" (down) */}
        <path d="M17 21 A7 7 0 0 0 31 21 Z" fill="var(--ab-ink)" />
        {/* hard-hat ridge */}
        <line x1="24" y1="13" x2="24" y2="20" stroke="var(--ab-ink)" strokeWidth="1.4" />
      </svg>
      {needs && <span className="worker-flag" aria-hidden>!</span>}
      <span className="worker-label">{agent.name}</span>
      <span className="visually-hidden">{lamp.label}</span>
    </button>
  );
}

/** A bench from above: a work surface, a tool rail, a job card pinned on it. */
function Bench({
  stage,
  jobs,
}: {
  stage: string;
  jobs: Ticket[];
}) {
  const tint = STAGE_TINT[stage] || "var(--ab-col-idle)";
  return (
    <div className="bench" data-stage={stage.toLowerCase().replace(/ /g, "-")}>
      <div className="bench-top" style={{ borderColor: "var(--ab-ink)" }} aria-hidden>
        <span
          className="bench-sign font-display"
          style={{ borderLeftColor: tint }}
        >
          {stage}
        </span>
        {/* the tool rail: a few pegs, count = jobs here */}
        <div className="bench-tools" aria-hidden>
          {Array.from({ length: Math.min(jobs.length, 6) }).map((_, i) => (
            <span key={i} className="bench-tool" style={{ background: tint }} />
          ))}
        </div>
      </div>
      <div className="bench-cards">
        {jobs.map((t) => (
          <span
            key={t.id}
            className="job-card"
            data-needs={t.needs_you_count > 0}
            title={t.name}
          >
            {t.name}
          </span>
        ))}
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
  const colWidth = 100 / n;
  const centerOf = (i: number) => colWidth * i + colWidth / 2;

  // one row of benches, left to right
  const jobsAt = (s: string) => board.tickets.filter((t) => t.stage === s);
  // workers stack vertically within their bench column
  const workersByStage: Record<string, AgentChipData[]> = {};
  for (const t of board.tickets) {
    for (const a of t.agents) {
      (workersByStage[t.stage] ||= []).push(a);
    }
  }
  const stageOf: Record<string, string> = {};
  for (const t of board.tickets) for (const a of t.agents) stageOf[a.pane] = t.stage;

  return (
    <div className="shop" aria-label="shop floor">
      {/* floor texture */}
      <div className="shop-tiles" aria-hidden />
      {/* aisle lines between benches */}
      {stations.slice(1).map((_, i) => (
        <span
          key={i}
          className="shop-aisle"
          style={{ left: `${colWidth * (i + 1)}%` }}
          aria-hidden
        />
      ))}

      {/* the benches */}
      <div className="shop-benches">
        {stations.map((s) => (
          <div key={s} className="shop-column" style={{ width: `${colWidth}%` }}>
            <Bench stage={s} jobs={jobsAt(s)} />
          </div>
        ))}
      </div>

      {/* the workers, each at its bench, stacked in that column */}
      <div className="shop-workers">
        {stations.map((s, ci) => {
          const list = workersByStage[s] || [];
          return list.map((a, wi) => (
            <div
              key={a.pane}
              className="worker-slot"
              style={{
                left: `${centerOf(ci)}%`,
                marginLeft: `${(wi % 2 === 0 ? -34 : 34)}px`,
                top: `${wi * 30}px`,
              }}
            >
              <Worker agent={a} onOpen={() => onSelect(a.pane)} />
            </div>
          ));
        })}
      </div>

      {board.tickets.length === 0 && (
        <p className="shop-empty">The floor is quiet. Hire an agent to open the shop.</p>
      )}
    </div>
  );
}
