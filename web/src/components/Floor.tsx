import { useMemo } from "react";
import type { AgentChipData, Board, Ticket } from "../types";
import { statusLamp } from "./TicketCard";

/**
 * The shop floor as a MAP, built from 2D tiles (Kenney top-down pack, CC0):
 * a tiled concrete floor with a wall border and walled-off rooms, placed on a
 * real 2D plan rather than a left-to-right row. Jobs are crates sitting in the
 * room they are in; workers are robot sprites that stand inside their room and
 * walk to the next room when the job advances.
 *
 * Tile size 64px. Positions come from data: each room has plan coordinates,
 * each occupant a slot inside it.
 */

const TILE = 64;

type Room = {
  stage: string;
  col: number;
  row: number;
  w: number;
  h: number;
};

// The floor plan: a shop building, rooms carved out on a 2D grid.
const PLAN: Room[] = [
  { stage: "To do", col: 0, row: 3, w: 3, h: 2 },
  { stage: "In progress", col: 3, row: 1, w: 4, h: 4 },
  { stage: "Testing", col: 3, row: 5, w: 2, h: 2 },
  { stage: "Review", col: 5, row: 5, w: 2, h: 2 },
  { stage: "Ready to push", col: 7, row: 3, w: 3, h: 2 },
  { stage: "Shipped", col: 7, row: 0, w: 3, h: 2 },
];
const COLS = 10;
const ROWS = 7;

const TINT: Record<string, string> = {
  "To do": "var(--ab-col-idle)",
  "In progress": "var(--ab-col-go)",
  Testing: "var(--ab-col-test)",
  Review: "var(--ab-col-review)",
  "Ready to push": "var(--ab-col-ready)",
  Shipped: "var(--ab-col-ship)",
};

const FLOOR = "/shop/floor.png";
const WALL = ["/shop/wall_0.png", "/shop/wall_1.png", "/shop/wall_2.png", "/shop/wall_3.png"];

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

/** A worker: the pack's robot sprite, top-down. Faces the room it works in. */
function Worker({ agent, onOpen }: { agent: AgentChipData; onOpen: () => void }) {
  const lamp = statusLamp(agent.agent_status, agent.needs_user);
  const working = agent.agent_status === "working";
  const needs = agent.needs_user;
  return (
    <button
      type="button"
      className={`worker ${working ? "is-working" : ""} ${needs ? "is-needs" : ""}`}
      onClick={onOpen}
      aria-label={`agent ${agent.name}`}
      title={`${agent.name} · ${lamp.label}`}
      style={{ ["--robot-tilt" as string]: `${(hash(agent.name) % 12) - 6}deg` }}
    >
      <img
        className="worker-sprite"
        src={working ? "/shop/robot_machine.png" : "/shop/robot_stand.png"}
        alt=""
        width={36}
        height={44}
        draggable={false}
      />
      {needs && <span className="worker-flag" aria-hidden>!</span>}
      <span className="worker-label">{agent.name}</span>
      <span className="visually-hidden">{lamp.label}</span>
    </button>
  );
}

/** A job as a crate sitting in a room. */
function JobBox({ ticket, onOpen }: { ticket: Ticket; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="job-crate"
      data-needs={ticket.needs_you_count > 0}
      onClick={onOpen}
      title={ticket.name}
      aria-label={`ticket ${ticket.name}`}
    >
      <svg viewBox="0 0 40 34" width="36" height="30" aria-hidden>
        <rect x="2" y="8" width="36" height="24" rx="2"
              fill="var(--ab-col-park)" stroke="var(--ab-ink)" strokeWidth="2" />
        <rect x="2" y="8" width="36" height="7" rx="2" fill="var(--ab-col-go)" />
        <line x1="2" y1="19" x2="38" y2="19" stroke="var(--ab-ink)" strokeWidth="1.4" />
        <line x1="14" y1="8" x2="14" y2="32" stroke="var(--ab-ink)" strokeWidth="1.1" opacity=".5" />
        <line x1="26" y1="8" x2="26" y2="32" stroke="var(--ab-ink)" strokeWidth="1.1" opacity=".5" />
      </svg>
      <span className="job-crate-label">{ticket.name}</span>
      {ticket.needs_you_count > 0 && <span className="job-crate-flag" aria-hidden>!</span>}
    </button>
  );
}

export function FloorView({
  board,
  onSelect,
}: {
  board: Board;
  onSelect: (pane: string | null) => void;
}) {
  const roomOf = useMemo(() => {
    const m: Record<string, Room> = {};
    for (const r of PLAN) m[r.stage] = r;
    return m;
  }, []);
  const byStage: Record<string, Ticket[]> = {};
  for (const t of board.tickets) (byStage[t.stage] ||= []).push(t);

  const PX = (n: number) => n * TILE;

  // Per-tile wall pattern: a border of wall tiles around every room.
  const wallTiles: { x: number; y: number; src: string; rot: number }[] = [];
  for (const r of PLAN) {
    for (let c = 0; c <= r.w; c++) {
      const cornerL = c === 0;
      const cornerR = c === r.w;
      for (const y of [r.row, r.row + r.h]) {
        if (cornerL || cornerR) {
          wallTiles.push({ x: (r.col + c) * TILE, y: y * TILE, src: WALL[0], rot: 0 });
        } else {
          wallTiles.push({ x: (r.col + c) * TILE, y: y * TILE, src: WALL[1], rot: 0 });
        }
      }
    }
    for (let rr = 0; rr < r.h; rr++) {
      for (const x of [r.col, r.col + r.w]) {
        wallTiles.push({ x: x * TILE, y: (r.row + rr) * TILE, src: WALL[2], rot: 0 });
      }
    }
  }

  // worker slots inside each room
  const slots: Record<string, { x: number; y: number }> = {};
  for (const t of board.tickets) {
    const r = roomOf[t.stage];
    if (!r) continue;
    t.agents.forEach((a, i) => {
      slots[a.pane] = {
        x: (r.col + 0.5 + (i % 2) * 1.4) * TILE,
        y: (r.row + r.h - 1.1 - Math.floor(i / 2) * 0.9) * TILE,
      };
    });
  }

  return (
    <div className="shop-map" aria-label="shop floor map">
      <div
        className="map-canvas"
        style={{ width: PX(COLS), height: PX(ROWS) }}
      >
        {/* tiled floor background */}
        <div
          className="map-floor"
          style={{ backgroundImage: `url(${FLOOR})`, backgroundSize: `${TILE}px ${TILE}px` }}
          aria-hidden
        />

        {/* rooms: a tinted concrete pad, with a wall border drawn from tiles */}
        {PLAN.map((r) => (
          <div
            key={r.stage}
            className="room-pad"
            style={{
              left: PX(r.col) + 6,
              top: PX(r.row) + 6,
              width: PX(r.w) - 12,
              height: PX(r.h) - 12,
              ["--room-tint" as string]: TINT[r.stage],
            }}
            aria-hidden
          >
            <span className="room-sign font-display">{r.stage}</span>
          </div>
        ))}
        {wallTiles.map((w, i) => (
          <img
            key={i}
            className="wall-tile"
            src={w.src}
            alt=""
            draggable={false}
            style={{ left: w.x, top: w.y, transform: `rotate(${w.rot}deg)` }}
            aria-hidden
          />
        ))}

        {/* crates sit in their room */}
        {Object.entries(byStage).map(([stage, jobs]) => {
          const r = roomOf[stage];
          if (!r) return null;
          return jobs.map((t, i) => (
            <div
              key={t.id}
              className="crate-slot"
              style={{
                left: PX(r.col) + 10 + (i % 3) * (TILE - 4),
                top: PX(r.row) + 26,
              }}
            >
              <JobBox
                ticket={t}
                onOpen={() => onSelect(t.agents[0]?.pane ?? null)}
              />
            </div>
          ));
        })}

        {/* workers walk between rooms when their job advances */}
        {board.agents
          .filter((a) => a.kind)
          .map((a) => {
            const s = slots[a.pane];
            if (!s) return null;
            return (
              <div
                key={a.pane}
                className="worker-token"
                style={{ left: s.x, top: s.y }}
              >
                <Worker agent={a} onOpen={() => onSelect(a.pane)} />
              </div>
            );
          })}
      </div>

      {board.tickets.length === 0 && (
        <p className="shop-empty">The floor is quiet. Hire an agent to open the shop.</p>
      )}
      <p className="shop-credit">
        Floor tiles: Kenney top-down pack (CC0)
      </p>
    </div>
  );
}
