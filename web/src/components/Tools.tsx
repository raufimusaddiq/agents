import { useState } from "react";
import {
  Badge,
  Box,
  Group,
  Loader,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import {
  IconChevronRight,
  IconCircleCheckFilled,
  IconCircleDashed,
  IconCircleXFilled,
  IconRobot,
} from "@tabler/icons-react";

export interface ToolCall {
  id: string;
  name: string;
  detail: string;
  result: string | null;
  error: boolean;
  result_lines?: number;
  edits?: { old: string; new: string }[];
  preview?: string;
  lines?: number;
  command?: string;
  agent?: string;
  task?: string;
  prompt?: string;
  background?: boolean;
  status?: string;
  ts?: string;
}

export interface Todo {
  id: string;
  subject: string;
  active: string;
  status: string;
}

const short = (p: string) =>
  p.replace(/^\/home\/[^/]+/, "~").replace(/^.*\/(?=[^/]+\/[^/]+$)/, "…/");
const n = (x: number, one: string, many = one + "s") =>
  `${x} ${x === 1 ? one : many}`;

/** The one-line summary Claude Code prints under a tool call, after its ⎿. */
function summary(t: ToolCall) {
  if (t.result == null) {
    return t.name === "Agent" || t.name === "Task"
      ? t.background
        ? "running in the background"
        : "running"
      : "running…";
  }
  if (t.error) {
    return (t.result.split("\n").find((l) => l.trim()) ?? "failed").slice(0, 160);
  }
  const lines = t.result_lines ?? t.result.split("\n").length;
  if (t.name === "Read") return `Read ${n(lines, "line")}`;
  if (t.name === "Grep" || t.name === "Glob") {
    return /^No (matches|files)/.test(t.result)
      ? t.result.split("\n")[0]
      : `Found ${n(lines, "result")}`;
  }
  if (t.name === "Edit" || t.name === "MultiEdit") {
    return `Updated ${
      t.edits && t.edits.length > 1 ? n(t.edits.length, "edit") : "the file"
    }`;
  }
  if (t.name === "Write") return `Wrote ${n(t.lines ?? 0, "line")}`;
  if (t.name === "Bash") {
    return t.result.trim() ? `${n(lines, "line")} of output` : "(no output)";
  }
  return t.result.split("\n").find((l) => l.trim())?.slice(0, 160) ?? "done";
}

/** An edit rendered as a unified diff: removed lines, then added ones. */
const editDiff = (t: ToolCall) =>
  (t.edits ?? [])
    .map((e) =>
      [
        ...(e.old ? e.old.split("\n").map((l) => `-${l}`) : []),
        ...(e.new ? e.new.split("\n").map((l) => `+${l}`) : []),
      ].join("\n"),
    )
    .join("\n@@\n");

function Diff({ text }: { text: string }) {
  return (
    <pre className="tool-diff" tabIndex={0}>
      {text.split("\n").map((l, i) => (
        <span
          key={i}
          className={
            l.startsWith("+")
              ? "dl add"
              : l.startsWith("-")
                ? "dl del"
                : l.startsWith("@@")
                  ? "dl hunk"
                  : "dl"
          }
        >
          {l}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

/** One tool call, drawn as Claude Code draws it: ⏺ Name(argument), then ⎿ the result. */
export function ToolBlock({ t, open: startOpen }: { t: ToolCall; open?: boolean }) {
  const isEdit = t.name === "Edit" || t.name === "MultiEdit";
  const [open, setOpen] = useState(startOpen ?? isEdit);
  const state = t.result == null ? "run" : t.error ? "err" : "ok";
  const body = isEdit ? (
    <Diff text={editDiff(t)} />
  ) : t.name === "Write" && t.preview ? (
    <Diff text={t.preview.split("\n").map((l) => `+${l}`).join("\n")} />
  ) : t.name === "Bash" ? (
    <pre className="tool-out" tabIndex={0}>
      {`$ ${t.command ?? t.detail}`}
      {t.result ? `\n${t.result.trimEnd()}` : ""}
    </pre>
  ) : t.result ? (
    <pre className="tool-out" tabIndex={0}>
      {t.result.trimEnd()}
    </pre>
  ) : null;
  const arg = t.name === "Bash" ? t.detail : short(t.detail);
  return (
    <div className={`tool ${state}`}>
      <UnstyledButton
        className="tool-head"
        onClick={() => body && setOpen(!open)}
        aria-expanded={body ? open : undefined}
        aria-label={`${t.name} ${arg}: ${summary(t)}`}
      >
        <span className="tool-dot" aria-hidden>
          ⏺
        </span>
        <span className="tool-name">{t.name}</span>
        {arg && <span className="tool-arg">({arg})</span>}
        <span className="tool-sum">
          {state === "run" && <Loader size={10} mr={4} />}
          {summary(t)}
        </span>
        {body && (
          <IconChevronRight
            size={12}
            className="tool-chev"
            style={{ transform: open ? "rotate(90deg)" : undefined }}
            aria-hidden
          />
        )}
      </UnstyledButton>
      {open && body && <div className="tool-body">{body}</div>}
    </div>
  );
}

/** A subagent the agent started: its task, live state, and what it reported. */
export function SubagentCard({ t }: { t: ToolCall }) {
  const [open, setOpen] = useState(false);
  const st = t.status ?? (t.result == null ? "running" : "done");
  return (
    <div className={`subagent ${st}`}>
      <Group gap={8} wrap="nowrap">
        <IconRobot size={16} aria-hidden />
        <Badge
          size="sm"
          variant="light"
          tt="none"
          color={st === "done" ? "teal" : st === "running" ? "blue" : "red"}
        >
          {t.agent}
        </Badge>
        <Text size="sm" fw={600} truncate style={{ flex: 1 }}>
          {t.task || "Subagent"}
        </Text>
        {st === "running" ? (
          <Loader size={14} type="dots" />
        ) : st === "done" ? (
          <IconCircleCheckFilled size={16} color="var(--mantine-color-teal-6)" />
        ) : (
          <IconCircleXFilled size={16} color="var(--mantine-color-red-6)" />
        )}
      </Group>
      <Text size="xs" c="dimmed" mt={2}>
        {st === "running"
          ? t.background
            ? "Working in the background"
            : "Working"
          : st === "done"
            ? "Finished"
            : `Ended: ${st}`}
      </Text>
      {(t.result || t.prompt) && (
        <UnstyledButton onClick={() => setOpen(!open)} mt={4} aria-expanded={open}>
          <Text size="xs" c="teal.6" fw={600}>
            {open ? "Hide" : t.result ? "Show what it reported" : "Show its brief"}
          </Text>
        </UnstyledButton>
      )}
      {open && (
        <pre className="tool-out" tabIndex={0}>
          {t.result || t.prompt}
        </pre>
      )}
    </div>
  );
}

/** A run of tool calls. More than three fold into one line unless it is the
 * live run; subagents and edits stay visible, since they are the point. */
export function ToolsRow({ tools, live }: { tools: ToolCall[]; live?: boolean }) {
  const [open, setOpen] = useState(false);
  const keep = (t: ToolCall) =>
    t.name === "Agent" || t.name === "Task" || t.name === "Edit" ||
    t.name === "MultiEdit" || t.name === "Write";
  const minor = tools.filter((t) => !keep(t));
  const fold = !open && minor.length > 3 && !live;
  const shown = fold ? tools.filter(keep) : tools;
  const counts = Object.entries(
    minor.reduce<Record<string, number>>(
      (m, t) => ({ ...m, [t.name]: (m[t.name] ?? 0) + 1 }),
      {},
    ),
  );
  return (
    <Stack gap={3}>
      {fold && (
        <UnstyledButton
          className="tool-fold"
          onClick={() => setOpen(true)}
          aria-label={`Show ${minor.length} tool calls`}
        >
          <span className="tool-dot" aria-hidden>
            ⏺
          </span>
          {minor.length} tool calls:{" "}
          {counts.map(([k, v]) => (v > 1 ? `${k} ×${v}` : k)).join(", ")}
          <IconChevronRight size={12} className="tool-chev" aria-hidden />
        </UnstyledButton>
      )}
      {shown.map((t) =>
        t.name === "Agent" || t.name === "Task" ? (
          <SubagentCard key={t.id} t={t} />
        ) : (
          <ToolBlock key={t.id} t={t} />
        ),
      )}
    </Stack>
  );
}

/** The agent's own task list, pinned at the top of the chat as it works. */
export function TodoCard({ todos }: { todos: Todo[] }) {
  const [open, setOpen] = useState(true);
  if (!todos.length) return null;
  const done = todos.filter((t) => t.status === "completed").length;
  const cur = todos.find((t) => t.status === "in_progress");
  return (
    <Box className="todo-card">
      <UnstyledButton onClick={() => setOpen(!open)} w="100%" aria-expanded={open}>
        <Group gap={8} wrap="nowrap">
          <Text size="sm" fw={600}>
            Tasks
          </Text>
          <Text size="xs" c="dimmed">
            {done}/{todos.length} done
          </Text>
          <div className="todo-bar" aria-hidden>
            <i style={{ width: `${(done / todos.length) * 100}%` }} />
          </div>
          <IconChevronRight
            size={14}
            style={{
              transform: open ? "rotate(90deg)" : undefined,
              transition: "transform 160ms",
            }}
            aria-hidden
          />
        </Group>
        {!open && cur && (
          <Text size="xs" mt={2} truncate>
            {cur.active || cur.subject}
          </Text>
        )}
      </UnstyledButton>
      {open && (
        <Stack gap={3} mt={6} role="list">
          {todos.map((t) => (
            <Group
              key={t.id}
              gap={8}
              wrap="nowrap"
              role="listitem"
              className={`todo ${t.status}`}
            >
              {t.status === "completed" ? (
                <IconCircleCheckFilled
                  size={15}
                  color="var(--mantine-color-teal-6)"
                  aria-label="done"
                />
              ) : t.status === "in_progress" ? (
                <Loader size={13} aria-label="in progress" />
              ) : (
                <IconCircleDashed
                  size={15}
                  color="var(--mantine-color-dimmed)"
                  aria-label="to do"
                />
              )}
              <Text size="xs" truncate title={t.subject}>
                {t.status === "in_progress" && t.active ? t.active : t.subject}
              </Text>
            </Group>
          ))}
        </Stack>
      )}
    </Box>
  );
}
