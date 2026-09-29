import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Textarea } from "@mantine/core";
import type { MenuItem } from "../types";
import { api } from "../api";

type DraftState = {
  msg: string;
  busy: boolean;
  error: string;
  syncError: boolean;
};
type Draft = {
  state: DraftState;
  mirrored: string;
  failed: boolean;
  queue: Promise<void>;
  listeners: Set<() => void>;
};

// A native composer survives closing its detail panel. Keep the corresponding
// draft and queue in memory too, so reopening cannot append to forgotten text.
const drafts = new Map<string, Draft>();
function draftFor(pane: string): Draft {
  let draft = drafts.get(pane);
  if (!draft) {
    draft = {
      state: { msg: "", busy: false, error: "", syncError: false },
      mirrored: "",
      failed: false,
      queue: Promise.resolve(),
      listeners: new Set(),
    };
    drafts.set(pane, draft);
  }
  return draft;
}
function updateDraft(draft: Draft, changes: Partial<DraftState>) {
  draft.state = { ...draft.state, ...changes };
  for (const listener of draft.listeners) listener();
}

/**
 * Chat composer with native trigger support.
 *
 * The harness owns its input buffer and its own completion menu (`/`, `@`, and
 * `$` in Codex). So the board does not send a finished message; it mirrors each
 * keystroke into the agent with `pane send-text` (no Enter) and lets the agent
 * draw its real menu. "Send" just presses Enter. That keeps `/`, `@` and `$`
 * behaving exactly as they do in the terminal.
 */
export function Composer({
  pane,
  readOnly = false,
  onSent,
}: {
  pane: string;
  readOnly?: boolean;
  onSent: () => void;
}) {
  const draft = draftFor(pane);
  const { msg, busy, error, syncError } = useSyncExternalStore(
    (listener) => {
      draft.listeners.add(listener);
      return () => {
        draft.listeners.delete(listener);
      };
    },
    () => draft.state,
  );
  const [items, setItems] = useState<MenuItem[]>([]);
  const poll = useRef<number | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const trigger = (() => {
    const m = /(^|\s)([/@$])([^\s]*)$/.exec(msg);
    return m ? { ch: m[2], query: m[3] } : null;
  })();

  // Mirror edits into the agent so its native menu reflects what we typed.
  // A single serialized queue keeps deltas in order without overlapping calls.
  function mirror(next: string) {
    const prev = draft.mirrored;
    if (next === prev) return;
    const before = Array.from(prev);
    const after = Array.from(next);
    let i = 0;
    while (i < before.length && i < after.length && before[i] === after[i]) i++;
    const removed = before.length - i;
    const added = after.slice(i).join("");
    draft.mirrored = next;
    updateDraft(draft, { msg: next });
    draft.queue = draft.queue.then(async () => {
      if (draft.failed) return;
      try {
        for (let r = 0; r < removed; r += 100) {
          await api.keys(
            pane,
            Array(Math.min(100, removed - r)).fill("backspace"),
          );
        }
        const characters = Array.from(added);
        for (let start = 0; start < characters.length; start += 2000) {
          await api.type(pane, characters.slice(start, start + 2000).join(""));
        }
      } catch {
        draft.failed = true;
        updateDraft(draft, {
          syncError: true,
          error:
            "Input could not be synchronized. Check the agent terminal before continuing.",
        });
      }
    });
  }

  useEffect(() => {
    if (poll.current) window.clearInterval(poll.current);
    if (!trigger) {
      setItems([]);
      return;
    }
    let active = true;
    const load = async () => {
      try {
        const r = await api.menu(pane);
        if (active) setItems(r.items.filter((i) => i.trigger === trigger.ch));
      } catch {
        if (active) setItems([]);
      }
    };
    const t = window.setTimeout(load, 350);
    poll.current = window.setInterval(load, 900);
    return () => {
      active = false;
      window.clearTimeout(t);
      if (poll.current) window.clearInterval(poll.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pane, trigger?.ch, trigger?.query]);

  async function send() {
    if (readOnly || draft.state.busy || draft.failed || !draft.state.msg.trim()) return;
    updateDraft(draft, { busy: true, error: "" });
    try {
      // Wait for any pending keystrokes to land, then submit. The text is
      // already in the agent's buffer, so Send is only Enter.
      await draft.queue;
      if (draft.failed)
        throw new Error(
          "Input synchronization failed. Check the agent terminal.",
        );
      await api.keys(pane, ["enter"]);
      updateDraft(draft, { msg: "" });
      draft.mirrored = "";
      setItems([]);
      onSent();
    } catch (e) {
      updateDraft(draft, { error: e instanceof Error ? e.message : "failed" });
    } finally {
      updateDraft(draft, { busy: false });
    }
  }

  async function pick(item: MenuItem) {
    // Replace the typed partial token; mirror the edit, keep editing.
    if (draft.state.busy || draft.failed) return;
    const label = item.label.startsWith(item.trigger)
      ? item.label
      : item.trigger + item.label;
    const next = msg.replace(/([/@$])[^\s]*$/, label + " ");
    mirror(next);
    setItems([]);
    taRef.current?.focus();
  }

  // Keep the agent's buffer in sync when the field is cleared or replaced
  // wholesale (e.g. select-all + delete, or the Send reset).
  function onChange(v: string) {
    mirror(v);
  }

  return (
    <div className="composer-wrap">
      {items.length > 0 && (
        <ul className="menu" aria-label="completions">
          {items.slice(0, 8).map((i, idx) => (
            <li key={`${i.label}-${idx}`}>
              <button
                type="button"
                className="menu-row"
                disabled={readOnly || busy || syncError}
                onClick={() => pick(i)}
                aria-label={`insert ${i.label}`}
              >
                <span className="menu-label mono">{i.label}</span>
                {i.detail && <span className="menu-detail">{i.detail}</span>}
                {i.kind && <span className="menu-kind">{i.kind}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="composer">
        <Textarea
          ref={taRef}
          style={{ flex: 1 }}
          autosize
          minRows={1}
          maxRows={5}
          placeholder="Message the agent — / @ $ work as in the harness"
          value={msg}
          disabled={readOnly || busy || syncError}
          onChange={(e) => {
            onChange(e.currentTarget.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !items.length) {
              e.preventDefault();
              send();
            }
          }}
          aria-label="message agent"
        />
        <button
          type="button"
          className="ab-btn hire-submit"
          disabled={readOnly || busy || syncError || !msg.trim()}
          onClick={send}
        >
          {busy ? "Sending…" : "Send"}
        </button>
      </div>
      {error && (
        <p className="hire-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
