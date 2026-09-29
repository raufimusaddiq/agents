import { useEffect, useRef, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Group,
  ScrollArea,
  Stack,
  Tabs,
  Text,
  Textarea,
} from "@mantine/core";
import type { Agent, ChatRow } from "../types";
import { api } from "../api";
import { AnswerCard } from "./AnswerCard";

function ChatThread({ rows }: { rows: ChatRow[] }) {
  return (
    <Stack gap="xs">
      {rows.map((r, i) => {
        if (r._fold) {
          return (
            <Text key={i} size="xs" c="dimmed" style={{ fontFamily: "monospace" }}>
              {r._fold}
              {r.path ? ` ${r.path}` : ""}
              {r.command ? ` $ ${r.command.slice(0, 80)}` : ""}
              {r.subagent_type ? ` [${r.subagent_type}]` : ""}
              {r._count && r._count > 1 ? ` ×${r._count}` : ""}
            </Text>
          );
        }
        const isUser = r.kind === "prompt";
        const isAnswer = r.kind === "answer" || r.kind === "question";
        return (
          <Box
            key={i}
            p="xs"
            style={{
              borderRadius: 6,
              alignSelf: isUser || isAnswer ? "flex-start" : "flex-end",
              maxWidth: "85%",
              background: isAnswer
                ? "var(--ab-answer)"
                : isUser
                  ? "var(--ab-user)"
                  : "var(--ab-reply)",
            }}
          >
            <Text size="xs" c="dimmed" fw={600}>
              {isAnswer ? "question" : isUser ? "you" : "agent"}
            </Text>
            <Text
              size="sm"
              style={{ whiteSpace: "pre-wrap" }}
              data-testid={`chat-${r.kind}`}
            >
              {r.answer || r.text}
            </Text>
          </Box>
        );
      })}
    </Stack>
  );
}

function CodeChanges({ agent }: { agent: Agent }) {
  const [diff, setDiff] = useState<{ title: string; body: string } | null>(null);
  const g = agent.git;
  if (!g) return <Text c="dimmed">No git repository for this agent.</Text>;
  return (
    <Stack gap="sm">
      <Card withBorder padding="sm">
        <Group justify="space-between">
          <Text fw={600} size="sm">
            {g.branch}
            {g.upstream ? ` → ${g.upstream}` : " (no upstream)"}
          </Text>
          <Group gap="xs">
            {g.ahead != null && <Badge size="xs">↑{g.ahead}</Badge>}
            {g.behind != null && <Badge size="xs">↓{g.behind}</Badge>}
            {g.unpushed_commits != null && (
              <Badge size="xs" color={g.unpushed_commits ? "orange" : "gray"}>
                {g.unpushed_commits} unpushed
              </Badge>
            )}
          </Group>
        </Group>
      </Card>
      <Text fw={600} size="sm">
        Changed files (read-only)
      </Text>
      <Stack gap={4}>
        {g.files.map((f) => (
          <Group
            key={f.path}
            justify="space-between"
            style={{ cursor: "pointer" }}
            onClick={() =>
              api
                .diff(g.repo, { path: f.path })
                .then((r) => setDiff({ title: f.path, body: r.diff }))
            }
          >
            <Text size="xs" style={{ fontFamily: "monospace" }} truncate>
              {f.status ? `[${f.status}] ` : ""}
              {f.path}
            </Text>
            <Text size="xs" c="dimmed">
              +{f.added} -{f.removed}
            </Text>
          </Group>
        ))}
        {g.files.length === 0 && (
          <Text size="xs" c="dimmed">
            Clean working tree.
          </Text>
        )}
        {g.files_capped && <Text size="xs" c="dimmed">(capped at 300 files)</Text>}
      </Stack>
      <Divider />
      <Text fw={600} size="sm">
        Last 15 commits
      </Text>
      <Stack gap={4}>
        {g.commits.map((c) => (
          <Group
            key={c.sha}
            justify="space-between"
            style={{ cursor: "pointer" }}
            onClick={() =>
              api
                .diff(g.repo, { sha: c.sha })
                .then((r) => setDiff({ title: c.short, body: r.diff }))
            }
          >
            <Text size="xs" truncate>
              <Text span c="dimmed" size="xs">
                {c.short}{" "}
              </Text>
              {c.subject}
            </Text>
            <Badge size="xs" color={c.pushed ? "green" : "orange"}>
              {c.pushed ? "pushed" : "local"}
            </Badge>
          </Group>
        ))}
      </Stack>
      {diff && (
        <Box>
          <Group justify="space-between">
            <Text fw={600} size="sm">
              Diff: {diff.title}
            </Text>
            <Button size="xs" variant="subtle" onClick={() => setDiff(null)}>
              close
            </Button>
          </Group>
          <ScrollArea h={300}>
            <Text
              size="xs"
              style={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}
            >
              {diff.body || "(empty)"}
            </Text>
          </ScrollArea>
        </Box>
      )}
    </Stack>
  );
}

function Workflow({ agent }: { agent: Agent }) {
  return (
    <Stack gap="sm">
      {agent.alerts.length === 0 && (
        <Text size="sm" c="dimmed">
          No workflow alerts for this agent.
        </Text>
      )}
      {agent.alerts.map((a) => (
        <Card key={a.key} withBorder padding="sm">
          <Text size="sm">{a.message}</Text>
          <Group mt="xs" gap="xs">
            <Button
              size="xs"
              variant="light"
              onClick={() => api.remind(a.pane).then(() => undefined)}
            >
              Remind agent
            </Button>
            <Button
              size="xs"
              variant="subtle"
              color="gray"
              onClick={() => api.dismiss(a.key).then(() => undefined)}
            >
              Dismiss
            </Button>
          </Group>
        </Card>
      ))}
      <Divider />
      <Text fw={600} size="sm">
        Capability notes
      </Text>
      {agent.notes.map((n) => (
        <Text key={n} size="xs" c="dimmed">
          {n}
        </Text>
      ))}
    </Stack>
  );
}

function Safety({ agent }: { agent: Agent }) {
  const g = agent.git;
  const status = agent.status as Record<string, unknown>;
  return (
    <Stack gap="sm">
      <Text fw={600} size="sm">
        Safe to restart?
      </Text>
      <Text size="sm">
        Uncommitted files: {g ? g.files.length : "no repo"}
      </Text>
      <Text size="sm">
        Commits on no remote:{" "}
        {g && g.has_remote ? (g.unpushed_commits ?? 0) : "none"}
      </Text>
      <Text size="sm">
        Working: {String(agent.status?.model ?? "unknown")}{" "}
        {String(agent.pane)}
      </Text>
      <Text size="sm">
        Context %: {status.context_pct != null ? `${status.context_pct}%` : "not reported"}
      </Text>
      {agent.usage_history.length > 1 && (
        <Box>
          <Text fw={600} size="sm">
            5h usage trend (45m)
          </Text>
          <Group gap={2} align="flex-end" h={40}>
            {agent.usage_history.map(([, pct], i) => (
              <Box
                key={i}
                w={6}
                h={(pct / 100) * 40}
                bg="blue"
                aria-hidden
              />
            ))}
          </Group>
        </Box>
      )}
      <Divider />
      <Text fw={600} size="sm">
        What the harness reports
      </Text>
      {Object.entries(agent.supports).map(([k, v]) => (
        <Text key={k} size="xs" c={v ? undefined : "dimmed"}>
          {k}: {v ? "yes" : `not reported by ${agent.kind}`}
        </Text>
      ))}
    </Stack>
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
    <Card
      withBorder
      padding="md"
      style={{ height: "100%", overflow: "auto" }}
      aria-label={`agent panel ${agent.name}`}
    >
      <Group justify="space-between">
        <Group>
          <Text fw={700}>{agent.name}</Text>
          <Badge>{agent.kind}</Badge>
          <Text size="xs" c="dimmed">
            {agent.pane}
          </Text>
        </Group>
        <Group>
          <Button
            size="xs"
            variant="light"
            onClick={() => api.focus(agent.pane)}
          >
            Open terminal (focus pane)
          </Button>
          <Button size="xs" variant="subtle" color="gray" onClick={onClose}>
            close
          </Button>
        </Group>
      </Group>

      {agent.ask && (
        <Box mt="sm">
          <AnswerCard pane={agent.pane} ask={agent.ask} onDone={load} />
        </Box>
      )}

      <Tabs defaultValue="chat" mt="md">
        <Tabs.List>
          <Tabs.Tab value="chat">Chat</Tabs.Tab>
          <Tabs.Tab value="code">Code changes</Tabs.Tab>
          <Tabs.Tab value="workflow">Workflow</Tabs.Tab>
          <Tabs.Tab value="safety">Safety</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="chat" pt="sm">
          <ScrollArea h={400}>
            <ChatThread rows={agent.chat} />
          </ScrollArea>
          <Group mt="sm" align="flex-end">
            <Textarea
              style={{ flex: 1 }}
              autosize
              minRows={1}
              maxRows={4}
              placeholder="Message the agent…"
              value={msg}
              onChange={(e) => setMsg(e.currentTarget.value)}
              aria-label="message agent"
            />
            <Button
              loading={loading}
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
              Send
            </Button>
          </Group>
          {agent.screen_tail && agent.screen_tail.length > 0 && (
            <Box mt="md">
              <Text size="xs" c="dimmed" fw={600}>
                Live screen tail (fallback)
              </Text>
              <ScrollArea h={160}>
                <Text
                  size="xs"
                  style={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}
                >
                  {agent.screen_tail.slice(-30).join("\n")}
                </Text>
              </ScrollArea>
            </Box>
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
    </Card>
  );
}
