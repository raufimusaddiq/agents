import { useEffect, useRef, useState } from "react";
import { Textarea } from "@mantine/core";
import type { MenuItem } from "../types";
import { api } from "../api";

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
  onSent,
}: {
  pane: string;
  onSent: () => void;
}) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<MenuItem[]>([]);
  const [error, setError] = useState("");
  const poll = useRef<number | null>(null);
  const mirrored = useRef("");
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const trigger = (() => {
    const m = /(^|\s)([/@$])([^\s]*)$/.exec(msg);
    return m ? { ch: m[2], query: m[3] } : null;
  })();

  // Mirror edits into the agent so its native menu reflects what we typed.
  // A single serialized queue keeps deltas in order without overlapping calls.
  const queue = useRef<Promise<void>>(Promise.resolve());
  function mirror(next: string) {
    const prev = mirrored.current;
    if (next === prev) return;
    let i = 0;
    while (i < prev.length && i < next.length && prev[i] === next[i]) i++;
    const removed = prev.length - i;
    const added = next.slice(i);
    mirrored.current = next;
    queue.current = queue.current.then(async () => {
      try {
        for (let r = 0; r < removed; r++) {
          await api.keys(pane, ["backspace"]);
        }
        if (added) await api.type(pane, added);
      } catch {
        /* the agent may be mid-turn; the user can retry */
      }
    });
  }

  useEffect(() => {
    if (poll.current) window.clearInterval(poll.current);
    if (!trigger) {
      setItems([]);
      return;
    }
    const load = async () => {
      try {
        const r = await api.menu(pane);
        setItems(r.items.filter((i) => i.trigger === trigger.ch));
      } catch {
        setItems([]);
      }
    };
    const t = window.setTimeout(load, 350);
    poll.current = window.setInterval(load, 900);
    return () => {
      window.clearTimeout(t);
      if (poll.current) window.clearInterval(poll.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pane, trigger?.ch, trigger?.query]);

  async function send() {
    if (!msg.trim()) return;
    setBusy(true);
    setError("");
    try {
      // Wait for any pending keystrokes to land, then submit. The text is
      // already in the agent's buffer, so Send is only Enter.
      await queue.current;
      await api.keys(pane, ["enter"]);
      setMsg("");
      mirrored.current = "";
      setItems([]);
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  async function pick(item: MenuItem) {
    // Replace the typed partial token; mirror the edit, keep editing.
    const next = msg.replace(/([/@$])[^\s]*$/, item.label + " ");
    mirror(next);
    setMsg(next);
    setItems([]);
    taRef.current?.focus();
  }

  // Keep the agent's buffer in sync when the field is cleared or replaced
  // wholesale (e.g. select-all + delete, or the Send reset).
  function onChange(v: string) {
    if (v === "") {
      // Drain whatever is in the agent's composer.
      const n = mirrored.current.length;
      mirrored.current = "";
      queue.current = queue.current.then(async () => {
        try {
          for (let i = 0; i < n; i++) await api.keys(pane, ["backspace"]);
        } catch {
          /* ignore */
        }
      });
    } else {
      mirror(v);
    }
    setMsg(v);
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
          disabled={busy}
          onClick={send}
        >
          {busy ? "Sending…" : "Send"}
        </button>
      </div>
      {error && <p className="hire-error">{error}</p>}
    </div>
  );
}
