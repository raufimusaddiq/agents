import { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Card,
  Group,
  Modal,
  ScrollArea,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import type { Board, Card as CardT, Worktree } from "../types";
import { api } from "../api";
import { CardView } from "./CardView";
import { useFlip } from "../flip";

function HireModal({
  opened,
  onClose,
  onDone,
  preset,
}: {
  opened: boolean;
  onClose: () => void;
  onDone: () => void;
  preset?: { folder: string; name: string } | null;
}) {
  const [kind, setKind] = useState("claude");
  const [workspace, setWorkspace] = useState("new");
  const [folder, setFolder] = useState("~");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.hire({ kind, workspace, folder, name, message });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Hire an agent">
      <Stack gap="sm">
        <Select
          label="Harness"
          data={["claude", "codex", "opencode"]}
          value={kind}
          onChange={(v) => setKind(v || "claude")}
          allowDeselect={false}
        />
        <Select
          label="Workspace"
          data={["new", "existing"]}
          value={workspace}
          onChange={(v) => setWorkspace(v || "new")}
          allowDeselect={false}
        />
        <TextInput
          label="Folder (under $HOME)"
          value={preset?.folder ?? folder}
          onChange={(e) => setFolder(e.currentTarget.value)}
          aria-label="hire folder"
        />
        <TextInput
          label="Name (1-40 chars)"
          value={preset?.name ?? name}
          onChange={(e) => setName(e.currentTarget.value)}
          aria-label="hire name"
        />
        <TextInput
          label="Optional first message"
          value={message}
          onChange={(e) => setMessage(e.currentTarget.value)}
        />
        {error && (
          <Text c="red" size="xs">
            {error}
          </Text>
        )}
        <Button onClick={submit} loading={busy}>
          Hire
        </Button>
      </Stack>
    </Modal>
  );
}

function WorktreeCards({
  worktrees,
  onRehire,
}: {
  worktrees: Worktree[];
  onRehire: (w: Worktree) => void;
}) {
  const [confirm, setConfirm] = useState<string | null>(null);
  const [err, setErr] = useState("");
  return (
    <Stack gap="xs">
      {worktrees.map((w) => (
        <Card
          key={w.path}
          withBorder
          padding="xs"
          data-flip-id={`wt:${w.path}`}
          aria-label={`worktree ${w.path}`}
        >
          <Text size="xs" fw={600} truncate>
            {w.branch || "(detached)"}
          </Text>
          <Text size="xs" c="dimmed" truncate>
            {w.path}
          </Text>
          <Group gap="xs" mt={4}>
            <Badge size="xs" color={w.uncommitted ? "orange" : "gray"}>
              {w.uncommitted} uncommitted
            </Badge>
            <Badge size="xs" variant="light">
              {w.unmerged} unmerged
            </Badge>
          </Group>
          <Group gap="xs" mt="xs">
            <Button size="xs" variant="light" onClick={() => onRehire(w)}>
              Rehire here
            </Button>
            <Button
              size="xs"
              color="red"
              variant="subtle"
              onClick={() => {
                setErr("");
                api.removeWorktree(w.path).catch((e) => {
                  setErr(e instanceof Error ? e.message : "failed");
                  setConfirm(w.path);
                });
              }}
            >
              Remove
            </Button>
          </Group>
          {confirm === w.path && err && (
            <Text c="red" size="xs" mt={4}>
              Remove refused: {err}
            </Text>
          )}
        </Card>
      ))}
      {worktrees.length === 0 && (
        <Text size="xs" c="dimmed">
          No parked worktrees.
        </Text>
      )}
    </Stack>
  );
}

export function BoardView({
  board,
  theme,
  onToggleTheme,
  selected,
  onSelect,
}: {
  board: Board;
  theme: string;
  onToggleTheme: () => void;
  selected: string | null;
  onSelect: (pane: string | null) => void;
}) {
  const [ticket, setTicket] = useState<string | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [onlyNeeds, setOnlyNeeds] = useState(false);
  const [hireOpen, setHireOpen] = useState(false);
  const [hirePreset, setHirePreset] = useState<{ folder: string; name: string } | null>(null);
  const ref = useFlip(JSON.stringify(board.cards.map((c) => [c.pane, c.column])));

  const ticketNames = Array.from(new Set(board.cards.map((c) => c.ticket))).sort();
  let cards = board.cards;
  if (ticket) cards = cards.filter((c) => c.ticket === ticket);
  if (kind) cards = cards.filter((c) => c.kind === kind);
  if (onlyNeeds) cards = cards.filter((c) => c.needs_user);

  return (
    <Stack gap="sm" h="100%">
      <Group justify="space-between" wrap="wrap">
        <Group gap="xs">
          <Text fw={700}>Agent Board</Text>
          {board.remote_on && (
            <Badge color="orange" variant="filled" data-testid="remote-badge">
              remote on
            </Badge>
          )}
        </Group>
        <Group gap="xs">
          <Select
            size="xs"
            placeholder="ticket"
            clearable
            data={ticketNames}
            value={ticket}
            onChange={setTicket}
            w={140}
          />
          <Select
            size="xs"
            placeholder="harness"
            clearable
            data={["claude", "codex", "opencode"]}
            value={kind}
            onChange={setKind}
            w={120}
          />
          <Button
            size="xs"
            variant={onlyNeeds ? "filled" : "light"}
            onClick={() => setOnlyNeeds((v) => !v)}
          >
            Only needs you
          </Button>
          <Button
            size="xs"
            variant="light"
            color="gray"
            data-testid="theme-toggle"
            aria-label="toggle theme"
            onClick={onToggleTheme}
          >
            {theme === "dark" ? "light" : "dark"}
          </Button>
          <Button
            size="xs"
            onClick={() => {
              setHirePreset(null);
              setHireOpen(true);
            }}
          >
            Hire
          </Button>
        </Group>
      </Group>

      {board.alerts.length > 0 && (
        <Card withBorder padding="xs" style={{ borderColor: "var(--mantine-color-red-5)" }}>
          <Text size="xs" fw={600}>
            Alerts (last 24h)
          </Text>
          {board.alerts.slice(0, 5).map((a) => (
            <Group key={a.key} justify="space-between">
              <Text size="xs">{a.message}</Text>
              <Group gap={4}>
                <Button
                  size="xs"
                  variant="subtle"
                  onClick={() => api.remind(a.pane)}
                >
                  remind
                </Button>
                <Button
                  size="xs"
                  variant="subtle"
                  color="gray"
                  onClick={() => api.dismiss(a.key)}
                >
                  dismiss
                </Button>
              </Group>
            </Group>
          ))}
        </Card>
      )}

      {board.frozen_roster && (
        <Card withBorder padding="xs" style={{ borderColor: "var(--mantine-color-orange-5)" }}>
          <Text size="sm" fw={600}>
            Restart detected. Roster frozen — rehire the agents below.
          </Text>
        </Card>
      )}

      <div
        ref={ref}
        style={{
          display: "flex",
          gap: 10,
          overflowX: "auto",
          alignItems: "flex-start",
          flex: 1,
        }}
      >
        {board.columns.map((col) => {
          const colCards = cards.filter((c) => c.column === col);
          return (
            <Box
              key={col}
              style={{ minWidth: 220, flex: "1 0 220px" }}
              aria-label={`column ${col}`}
            >
              <Group justify="space-between" mb={6}>
                <Text size="sm" fw={600}>
                  {col}
                </Text>
                <Badge size="xs" variant="light">
                  {col === "Parked" ? board.worktrees.length : colCards.length}
                </Badge>
              </Group>
              <ScrollArea h="calc(100vh - 220px)">
                <Stack gap="xs">
                  {col === "Parked" ? (
                    <WorktreeCards
                      worktrees={board.worktrees}
                      onRehire={(w) => {
                        setHirePreset({
                          folder: w.path,
                          name: w.branch.replace(/^refs\/heads\//, "").split("/").pop() || "agent",
                        });
                        setHireOpen(true);
                      }}
                    />
                  ) : (
                    colCards.map((c: CardT) => (
                      <CardView
                        key={c.pane}
                        card={c}
                        selected={selected === c.pane}
                        onOpen={() => onSelect(c.pane)}
                      />
                    ))
                  )}
                </Stack>
              </ScrollArea>
            </Box>
          );
        })}
      </div>

      {board.closed.length > 0 && (
        <Group gap="xs">
          <Text size="xs" c="dimmed">
            Closed (rehirable 24h):
          </Text>
          {board.closed.map((c) => (
            <Badge
              key={c.pane}
              size="sm"
              style={{ cursor: "pointer" }}
              onClick={() => api.rehire(c.pane)}
            >
              rehire {c.name || c.pane}
            </Badge>
          ))}
        </Group>
      )}

      <HireModal
        opened={hireOpen}
        onClose={() => setHireOpen(false)}
        onDone={() => undefined}
        preset={hirePreset}
      />
    </Stack>
  );
}
