import { useEffect, useRef, useState } from "react";
import { Badge, Box, Group, Loader, Stack, Text } from "@mantine/core";
import { IconCircleCheck, IconTerminal2 } from "@tabler/icons-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import { ToolsRow, TodoCard, type Todo, type ToolCall } from "./Tools";

interface Question {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options: { label: string; description?: string }[];
}

export interface ChatItem {
  role: "user" | "assistant" | "command" | "tools" | "screen" | "question" | "shell";
  text?: string;
  output?: string | null;
  ts?: string;
  queued?: boolean;
  tools?: ToolCall[];
  id?: string;
  questions?: Question[];
  answers?: Record<string, string> | null;
  cancelled?: boolean;
}

function ago(ts?: string) {
  if (!ts) return "";
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function QuestionCard({ m }: { m: ChatItem }) {
  const qs = m.questions ?? [];
  return (
    <div className="chat-question" role="group" aria-label="Question">
      <Stack gap={8}>
        {qs.map((q, qi) => {
          const answer = m.answers?.[q.question];
          const parts =
            answer == null ? [] : q.multiSelect ? answer.split(", ") : [answer];
          const own = parts.filter((a) => !q.options.some((o) => o.label === a));
          return (
            <Stack key={qi} gap={6}>
              <Group gap={6}>
                {q.header && (
                  <Badge variant="light" color="yellow" tt="none">
                    {q.header}
                  </Badge>
                )}
                {q.multiSelect && (
                  <Text size="xs" c="dimmed">
                    pick any
                  </Text>
                )}
                {m.cancelled && (
                  <Badge variant="light" color="gray" size="sm">
                    dismissed
                  </Badge>
                )}
              </Group>
              <Text size="sm" fw={600}>
                {q.question}
              </Text>
              {q.options.map((o, oi) => {
                const picked = parts.includes(o.label);
                return (
                  <Group
                    key={oi}
                    gap={8}
                    wrap="nowrap"
                    align="flex-start"
                    px={8}
                    py={4}
                    className={picked ? "q-opt is-picked" : "q-opt"}
                    style={{ opacity: answer && !picked ? 0.6 : 1 }}
                  >
                    {picked ? (
                      <IconCircleCheck
                        size={16}
                        color="var(--mantine-color-teal-6)"
                        style={{ marginTop: 2 }}
                        aria-label="picked"
                      />
                    ) : (
                      <Box w={16} />
                    )}
                    <div>
                      <Text size="sm" fw={picked ? 600 : 500}>
                        {oi + 1}. {o.label}
                      </Text>
                      {o.description && (
                        <Text size="xs" c="dimmed">
                          {o.description}
                        </Text>
                      )}
                    </div>
                  </Group>
                );
              })}
              {own.length > 0 && (
                <Text size="sm">
                  You answered:{" "}
                  <Text span fw={600} inherit>
                    {own.join(", ")}
                  </Text>
                </Text>
              )}
            </Stack>
          );
        })}
        {m.ts && (
          <Text size="10px" c="dimmed">
            {ago(m.ts)} ago
          </Text>
        )}
      </Stack>
    </div>
  );
}

function Bubble({ m, sending }: { m: ChatItem; sending?: boolean }) {
  if (m.role === "tools") return <ToolsRow tools={m.tools!} />;
  if (m.role === "command") {
    return (
      <Text size="xs" ff="monospace" c="dimmed" pl={4}>
        <IconTerminal2 size={11} /> {m.text}
      </Text>
    );
  }
  if (m.role === "screen") {
    return <pre className="screen-bubble">{m.text}</pre>;
  }
  if (m.role === "shell" || (sending && m.text?.startsWith("!"))) {
    const cmd = m.role === "shell" ? m.text : m.text!.slice(1);
    return (
      <Box
        className="shell-bubble"
        style={{ opacity: sending ? 0.6 : 1 }}
        role="group"
        aria-label={`Shell command ${cmd}`}
      >
        <div className="shell-cmd">
          <span className="bang" aria-hidden>
            !
          </span>
          {cmd}
        </div>
        {m.output ? (
          <pre tabIndex={0} aria-label="Command output">
            {m.output.trimEnd()}
          </pre>
        ) : (
          <Text size="10px" c="dimmed" mt={2}>
            {sending ? "sending" : m.output === "" ? "no output" : "running…"}
          </Text>
        )}
      </Box>
    );
  }
  const mine = m.role === "user";
  return (
    <div
      className="chat-turn"
      style={{
        alignSelf: mine ? "flex-end" : "flex-start",
        maxWidth: mine ? "85%" : "100%",
      }}
    >
      <div
        className={mine ? "bubble mine" : "bubble theirs"}
        style={{ opacity: sending ? 0.6 : 1 }}
      >
        {mine ? (
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {m.text}
          </Text>
        ) : (
          <div className="md">
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                pre: ({ node: _n, ...p }) => (
                  <pre tabIndex={0} aria-label="Code block" {...p} />
                ),
              }}
            >
              {m.text}
            </Markdown>
          </div>
        )}
      </div>
      <Text size="10px" c="dimmed" ta={mine ? "right" : "left"} mt={2}>
        {sending
          ? "sending"
          : `${m.queued ? "sent while busy, " : ""}${
              m.ts ? ago(m.ts) + " ago" : ""
            }`}
      </Text>
    </div>
  );
}

/**
 * The agent's conversation, read from its transcript. Sending goes through
 * `herdr agent prompt`, which types into the pane exactly as in the terminal.
 */
export function ChatView({
  pane,
  label,
  status,
}: {
  pane: string;
  label: string;
  status: string;
}) {
  const [items, setItems] = useState<ChatItem[] | null>(null);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [source, setSource] = useState("");
  const view = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  const sig = useRef("");

  useEffect(() => {
    let live = true;
    setItems(null);
    sig.current = "";
    setTodos([]);
    const tick = async () => {
      try {
        const r = await api.chat(pane);
        if (!live || !r.ok) return;
        const s = JSON.stringify([r.items, r.todos]);
        if (s === sig.current) return;
        sig.current = s;
        setItems(r.items as ChatItem[]);
        setSource(r.source);
        setTodos((r.todos as Todo[]) ?? []);
      } catch {
        /* keep the last good view */
      }
    };
    tick();
    const iv = window.setInterval(tick, 1500);
    return () => {
      live = false;
      window.clearInterval(iv);
    };
  }, [pane]);

  // Follow new messages only when already at the bottom, so scrolling back to
  // read is never yanked away.
  useEffect(() => {
    if (pinned.current && view.current) {
      view.current.scrollTop = view.current.scrollHeight;
    }
  }, [items, status]);

  return (
    <div
      ref={view}
      className="chat-scroll"
      data-testid="chat-scroll"
      onScroll={() => {
        const v = view.current;
        if (v) pinned.current = v.scrollHeight - v.clientHeight - v.scrollTop < 40;
      }}
    >
      {todos.length > 0 && (
        <div className="todo-pin">
          <TodoCard todos={todos} />
        </div>
      )}
      <Stack
        gap="sm"
        p="sm"
        role="log"
        aria-live="polite"
        aria-label={`Conversation with ${label}`}
      >
        {items === null && (
          <Group justify="center" py="xl">
            <Loader size="sm" type="dots" />
          </Group>
        )}
        {items?.length === 0 && (
          <Text size="sm" c="dimmed" ta="center" py="xl">
            No messages yet. Say something below.
          </Text>
        )}
        {source === "screen" && (
          <Text size="xs" c="dimmed">
            Chat history is read from the harness transcript; this agent shows
            its screen instead.
          </Text>
        )}
        {items?.map((m, i) =>
          m.role === "question" ? (
            <QuestionCard key={m.id ?? i} m={m} />
          ) : (
            <Bubble key={i} m={m} />
          ),
        )}
      </Stack>
    </div>
  );
}
