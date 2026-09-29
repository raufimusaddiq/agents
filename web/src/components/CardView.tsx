import { Badge, Box, Group, Text, Tooltip } from "@mantine/core";
import type { Card } from "../types";

const KIND_COLOR: Record<string, string> = {
  claude: "orange",
  codex: "teal",
  opencode: "grape",
};

function StationStrip({ stations }: { stations: [string, string][] }) {
  const color: Record<string, string> = {
    done: "var(--mantine-color-green-6)",
    in_progress: "var(--mantine-color-blue-6)",
    waiting: "var(--mantine-color-yellow-6)",
    todo: "var(--mantine-color-gray-4)",
    skipped: "var(--mantine-color-red-6)",
    not_needed: "var(--mantine-color-gray-3)",
  };
  return (
    <Group gap={3} aria-label="workflow steps">
      {stations.map(([name, state]) => (
        <Tooltip key={name} label={`${name}: ${state}`} withArrow>
          <Box
            w={9}
            h={9}
            style={{
              borderRadius: 0,
              background: color[state] || "gray",
            }}
            aria-hidden
          />
        </Tooltip>
      ))}
    </Group>
  );
}

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
  return (
    <Box
      data-flip-id={card.pane}
      data-testid="card"
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
      p="xs"
      style={{
        cursor: "pointer",
        borderRadius: 6,
        background: "var(--ab-card)",
        border: selected
          ? "2px solid var(--mantine-color-blue-6)"
          : "1px solid var(--ab-border)",
        boxShadow: "var(--ab-shadow)",
      }}
    >
      <Group justify="space-between" gap={6} wrap="nowrap">
        <Text fw={600} size="sm" truncate>
          {card.name}
        </Text>
        <Badge
          size="xs"
          color={KIND_COLOR[card.kind] || "gray"}
          variant="light"
        >
          {card.kind || "?"}
        </Badge>
      </Group>
      <Text size="xs" c="dimmed" truncate>
        {card.repo ? card.repo.split("/").pop() : card.cwd}
        {card.branch ? ` · ${card.branch}` : ""}
      </Text>
      <Group justify="space-between" mt={6} gap={6}>
        <StationStrip stations={card.stations} />
        {card.context_pct != null && (
          <Text size="xs" c="dimmed">
            ctx {card.context_pct}%
          </Text>
        )}
      </Group>
      {skipped.length > 0 && (
        <Group gap={4} mt={6}>
          {skipped.map(([name]) => (
            <Badge key={name} size="xs" color="red" variant="filled">
              {name}
            </Badge>
          ))}
        </Group>
      )}
      <Text size="xs" c="dimmed" mt={6} lineClamp={2}>
        {card.last_line}
      </Text>
      <Text size="xs" c="dimmed" mt={4}>
        {card.time_in_column}s in column
      </Text>
    </Box>
  );
}
