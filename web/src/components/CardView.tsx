import type { Card } from "../types";

const KIND_VAR: Record<string, string> = {
  claude: "var(--ab-badge-claude)",
  codex: "var(--ab-badge-codex)",
  opencode: "var(--ab-badge-opencode)",
};

export function CardView({
  card,
  selected,
  onOpen,
}: {
  card: Card;
  selected: boolean;
  onOpen: () => void;
}) {
  const skipped = card.stations.filter(([, s]) => s === "skipped");
  const stationIndex = card.stations.findIndex(
    ([, s]) => s === "in_progress" || s === "waiting",
  );
  const stage = stationIndex >= 0 ? card.stations[stationIndex][0] : null;

  return (
    <div
      className="ab-card work-order"
      data-flip-id={card.pane}
      data-testid="card"
      data-selected={selected}
      aria-label={`card ${card.name}`}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <header className="wo-head">
        <span className="wo-name font-display">{card.name}</span>
        <span
          className="wo-kind"
          style={{ color: KIND_VAR[card.kind] || "var(--ab-dim)" }}
        >
          {card.kind || "?"}
        </span>
      </header>

      <p className="wo-repo mono">
        {card.repo ? card.repo.split("/").pop() : card.cwd}
        {card.branch ? ` · ${card.branch}` : ""}
      </p>

      {stage && <p className="wo-stage">{stage}</p>}

      <div className="wo-meta">
        {card.needs_user && <span className="wo-flag">needs you</span>}
        {card.context_pct != null && (
          <span className="wo-ctx">{card.context_pct}% ctx</span>
        )}
      </div>

      {skipped.length > 0 && (
        <p className="wo-skipped">
          skipped: {skipped.map(([name]) => name).join(", ")}
        </p>
      )}

      <p className="wo-last">{card.last_line}</p>
      <p className="wo-time">{card.time_in_column}s in column</p>
    </div>
  );
}
