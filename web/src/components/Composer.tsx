import { useEffect, useRef, useState } from "react";
import {
  ActionIcon,
  Badge,
  CloseButton,
  Combobox,
  Group,
  Loader,
  ScrollArea,
  Text,
  Textarea,
  Tooltip,
  useCombobox,
} from "@mantine/core";
import {
  IconFile,
  IconFolder,
  IconPaperclip,
  IconSend,
  IconSlash,
  IconTerminal2,
} from "@tabler/icons-react";
import { api } from "../api";

interface Attachment {
  id: string;
  name: string;
  size: number;
  image: boolean;
  preview?: string;
  path?: string;
  error?: string;
}

interface Suggestion {
  name: string;
  description?: string;
  source?: string;
}

interface Token {
  kind: "command" | "file";
  q: string;
  start: number;
  end: number;
}

const kb = (n: number) =>
  n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

async function upload(f: File): Promise<{ path?: string; error?: string }> {
  if (f.size > 25 * 1048576) return { error: "over 25 MB" };
  try {
    const r = await fetch("/api/upload", {
      method: "POST",
      headers: {
        "X-Filename": encodeURIComponent(f.name || "pasted.png"),
        "Content-Type": "application/octet-stream",
      },
      body: f,
    });
    const j = await r.json();
    return j.ok ? { path: j.path } : { error: j.error || "upload failed" };
  } catch {
    return { error: "upload failed" };
  }
}

// `/` counts only as the first word (like the harness); `@` anywhere after a
// space or at the start.
export function tokenAt(text: string, caret: number): Token | null {
  const before = text.slice(0, caret);
  const m = /(^|\s)([/@$])([^\s]*)$/.exec(before);
  if (!m) return null;
  const start = m.index + m[1].length;
  if (m[2] === "/" && before.slice(0, start).trim()) return null;
  return {
    kind: m[2] === "/" ? "command" : "file",
    q: m[3],
    start,
    end: caret,
  };
}

/**
 * The chat input, with the harness's `/` command and `@` file autocomplete,
 * scoped to the agent's own working folder. Sending goes straight to the agent
 * with `herdr agent prompt`, exactly as if typed in its terminal.
 */
export function Composer({
  pane,
  label,
  onSend,
  cwd,
  history = [],
  readOnly = false,
}: {
  pane: string;
  label: string;
  onSend: () => void;
  cwd?: string;
  history?: string[];
  readOnly?: boolean;
}) {
  const recall = useRef(-1); // ↑ in an empty box walks back through what you sent
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [drag, setDrag] = useState(false);
  const [token, setToken] = useState<Token | null>(null);
  const [items, setItems] = useState<Suggestion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const combobox = useCombobox();
  const req = useRef(0);
  // Claude Code's bash mode: a leading `!` runs the rest as a shell command.
  const shell = draft.startsWith("!");
  const uploading = files.some((f) => !f.path && !f.error);

  const add = (list: FileList | File[]) => {
    for (const f of Array.from(list)) {
      const id = Math.random().toString(36).slice(2);
      const image = f.type.startsWith("image/");
      setFiles((x) => [
        ...x,
        {
          id,
          name: f.name || "pasted image.png",
          size: f.size,
          image,
          preview: image ? URL.createObjectURL(f) : undefined,
        },
      ]);
      upload(f).then((r) =>
        setFiles((x) => x.map((a) => (a.id === id ? { ...a, ...r } : a))),
      );
    }
  };
  const drop = (id: string) =>
    setFiles((x) => {
      const a = x.find((f) => f.id === id);
      if (a?.preview) URL.revokeObjectURL(a.preview);
      return x.filter((f) => f.id !== id);
    });

  useEffect(() => {
    if (!token) {
      combobox.closeDropdown();
      return;
    }
    const id = ++req.current;
    const t = window.setTimeout(async () => {
      try {
        const r = await api.suggest(pane, token.kind, token.q);
        if (id !== req.current) return;
        setItems(r.items || []);
        if (r.items?.length) {
          combobox.openDropdown();
          requestAnimationFrame(() => combobox.selectFirstOption());
        } else {
          combobox.closeDropdown();
        }
      } catch {
        setItems([]);
      }
    }, 90);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token?.kind, token?.q, pane]);

  const accept = (name: string) => {
    if (!token) return;
    const dir = token.kind === "file" && name.endsWith("/");
    const insert = (token.kind === "command" ? "/" : "@") + name + (dir ? "" : " ");
    const next = draft.slice(0, token.start) + insert + draft.slice(token.end);
    const caret = token.start + insert.length;
    setDraft(next);
    setToken(dir ? tokenAt(next, caret) : null);
    if (!dir) {
      combobox.closeDropdown();
      setItems([]);
    }
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(caret, caret);
    });
  };

  async function send() {
    const ready = shell ? [] : files.filter((f) => f.path);
    const said = [
      ...ready.map((f) => `@${f.path}`),
      draft.trim() ||
        (ready.length
          ? `See the attached file${ready.length > 1 ? "s." : "."}`
          : ""),
    ]
      .filter(Boolean)
      .join(" ");
    if (!said || busy || (uploading && !shell) || said === "!") return;
    setBusy(true);
    setError("");
    try {
      await api.prompt(pane, said);
      setDraft("");
      recall.current = -1;
      setToken(null);
      if (!shell) {
        files.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
        setFiles([]);
      }
      onSend();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  if (readOnly) {
    return (
      <p className="panel-note">
        This agent is closed or blocked; open its terminal to reply.
      </p>
    );
  }

  return (
    <div
      className={`composer-wrap${shell ? " shell" : ""}${drag ? " drag" : ""}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDrag(true);
        }
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDrag(false);
      }}
      onDrop={(e) => {
        if (e.dataTransfer.files.length) {
          e.preventDefault();
          setDrag(false);
          if (!shell) add(e.dataTransfer.files);
        }
      }}
    >
      {shell && (
        <div className="shell-badge">
          <IconTerminal2 size={13} aria-hidden /> <b>Bash mode</b>
          <span>
            runs in {cwd ? cwd.replace(/^\/home\/[^/]+/, "~") : "the agent's folder"}
          </span>
        </div>
      )}
      {files.length > 0 && !shell && (
        <div className="attach-row" aria-label="Attachments">
          {files.map((f) => (
            <div key={f.id} className={`attach${f.error ? " bad" : ""}`}>
              {f.image && f.preview ? (
                <img src={f.preview} alt="" />
              ) : (
                <IconFile size={18} aria-hidden />
              )}
              <div className="attach-meta">
                <Text size="xs" fw={600} truncate title={f.name}>
                  {f.name}
                </Text>
                <Text size="10px" c={f.error ? "red" : "dimmed"}>
                  {f.error ?? (f.path ? kb(f.size) : "uploading…")}
                </Text>
              </div>
              {!f.path && !f.error && <Loader size={12} />}
              <CloseButton
                size="sm"
                aria-label={`Remove ${f.name}`}
                onClick={() => drop(f.id)}
              />
            </div>
          ))}
        </div>
      )}
      {drag && <div className="drop-hint">Drop to attach</div>}
      <Group gap="xs" align="flex-end" wrap="nowrap">
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.currentTarget.files) add(e.currentTarget.files);
            e.currentTarget.value = "";
          }}
        />
        <Tooltip
          label={
            shell
              ? "Attachments are off in bash mode"
              : "Attach files or images (or paste, or drop them here)"
          }
        >
          <ActionIcon
            size={34}
            variant="default"
            onClick={() => picker.current?.click()}
            disabled={shell}
            aria-label="Attach files"
          >
            <IconPaperclip size={17} />
          </ActionIcon>
        </Tooltip>
        <Combobox store={combobox} onOptionSubmit={accept} position="top-start" width="target">
          <Combobox.Target targetType="input">
            <Textarea
              ref={box}
              aria-label={`Message ${label}`}
              autosize
              minRows={1}
              maxRows={8}
              style={{ flex: 1 }}
              placeholder={`Message ${label}   / commands, @ files, ! shell, Enter to send`}
              value={draft}
              onPaste={(e) => {
                const fl = e.clipboardData.files;
                if (fl.length && !shell) {
                  e.preventDefault();
                  add(fl);
                }
              }}
              onChange={(e) => {
                recall.current = -1;
                const v = e.currentTarget.value;
                setDraft(v);
                setToken(tokenAt(v, e.currentTarget.selectionStart));
              }}
              onClick={(e) =>
                setToken(tokenAt(e.currentTarget.value, e.currentTarget.selectionStart))
              }
              onBlur={() => combobox.closeDropdown()}
              onKeyDown={(e) => {
                const open = combobox.dropdownOpened && items.length > 0 && token !== null;
                if (open && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey))) {
                  e.preventDefault();
                  if (combobox.getSelectedOptionIndex() < 0) combobox.selectFirstOption();
                  combobox.clickSelectedOption();
                  return;
                }
                if (open && e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  setToken(null);
                  return;
                }
                if (
                  shell &&
                  (e.key === "Escape" || (e.key === "Backspace" && draft === "!"))
                ) {
                  e.preventDefault();
                  setDraft("");
                  return;
                }
                if (
                  !open &&
                  (e.key === "ArrowUp" || e.key === "ArrowDown") &&
                  history.length &&
                  (draft === "" || recall.current >= 0) &&
                  !draft.includes("\n")
                ) {
                  const next =
                    e.key === "ArrowUp"
                      ? Math.min(history.length - 1, recall.current + 1)
                      : recall.current - 1;
                  e.preventDefault();
                  recall.current = next;
                  setDraft(next < 0 ? "" : history[history.length - 1 - next]);
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  recall.current = -1;
                  send();
                }
              }}
            />
          </Combobox.Target>
          <Combobox.Dropdown p={4} hidden={!token || !items.length}>
            <Combobox.Options aria-label={token?.kind === "command" ? "Commands" : "Files"}>
              <ScrollArea.Autosize mah={280} type="auto">
                {items.map((it) => (
                  <Combobox.Option value={it.name} key={it.name} style={{ paddingBlock: 6 }}>
                    {token?.kind === "command" ? (
                      <Group gap={8} wrap="nowrap" align="flex-start">
                        <IconSlash size={14} style={{ marginTop: 3, flexShrink: 0 }} aria-hidden />
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <Group gap={6} wrap="nowrap">
                            <Text size="sm" ff="monospace" fw={600} truncate>
                              /{it.name}
                            </Text>
                            {it.source && it.source !== "personal" && (
                              <Badge
                                size="xs"
                                variant="light"
                                color={
                                  it.source === "built-in"
                                    ? "gray"
                                    : it.source === "plugin"
                                      ? "grape"
                                      : "blue"
                                }
                                tt="none"
                              >
                                {it.source}
                              </Badge>
                            )}
                          </Group>
                          {it.description && (
                            <Text size="xs" c="dimmed" lineClamp={1}>
                              {it.description}
                            </Text>
                          )}
                        </div>
                      </Group>
                    ) : (
                      <Group gap={8} wrap="nowrap">
                        {it.name.endsWith("/") ? (
                          <IconFolder size={14} aria-hidden />
                        ) : (
                          <IconFile size={14} aria-hidden />
                        )}
                        <Text size="sm" ff="monospace" truncate title={it.name}>
                          {it.name}
                        </Text>
                      </Group>
                    )}
                  </Combobox.Option>
                ))}
              </ScrollArea.Autosize>
            </Combobox.Options>
            <Text size="10px" c="dimmed" px={8} pt={4}>
              ↑↓ to move, Tab or Enter to insert, Esc to close
            </Text>
          </Combobox.Dropdown>
        </Combobox>
        <Tooltip
          label={
            shell
              ? "Run it in the agent's terminal"
              : "Send straight to the agent, as if typed in its terminal"
          }
        >
          <ActionIcon
            size={34}
            onClick={send}
            loading={busy}
            color={shell ? "pink" : undefined}
            disabled={
              (shell ? draft.trim().length < 2 : !draft.trim() && !files.some((f) => f.path)) ||
              (!shell && uploading)
            }
            aria-label={shell ? "Run command" : "Send"}
          >
            {shell ? <IconTerminal2 size={17} /> : <IconSend size={17} />}
          </ActionIcon>
        </Tooltip>
      </Group>
      {error && <p className="hire-error">{error}</p>}
    </div>
  );
}
