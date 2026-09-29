import { useState } from "react";
import { Checkbox, Radio, Stack, TextInput } from "@mantine/core";
import type { Ask } from "../types";
import { api } from "../api";

export function AnswerCard({
  pane,
  ask,
  onDone,
}: {
  pane: string;
  ask: Ask;
  onDone: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      let answer: unknown;
      if (ask.multi) {
        answer = { multi: checked, text: typed || undefined };
      } else if (typed && selected === null) {
        answer = { text: typed };
      } else if (selected !== null) {
        answer = { option: selected, text: typed || undefined };
      } else {
        answer = { text: typed };
      }
      await api.answer(pane, answer, ask);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  async function sendKey(key: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.keys(pane, [key]);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to send key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="answer-card" aria-label={`needs you ${pane}`}>
      <p className="answer-flag">
        <span className="ar-mark" aria-hidden>
          ▮
        </span>
        This agent needs you
      </p>
      {ask.tabs.length > 0 && (
        <div className="answer-tabs">
          {ask.tabs.map((t) => (
            <span key={t} className="park-chip">
              {t}
            </span>
          ))}
        </div>
      )}
      <p className="answer-q">{ask.question}</p>
      {ask.options.length > 0 ? (
        <Stack gap="xs">
          {ask.options.map((o, i) => (
            <div key={i}>
              {ask.multi ? (
                <Checkbox
                  label={o.label}
                  checked={checked.includes(o.label)}
                  onChange={(e) =>
                    setChecked(
                      e.currentTarget.checked
                        ? [...checked, o.label]
                        : checked.filter((x) => x !== o.label),
                    )
                  }
                />
              ) : (
                <Radio
                  name={`ask-${pane}`}
                  value={String(o.number ?? i)}
                  label={o.label}
                  checked={selected === (o.number ?? i)}
                  onChange={() => setSelected(o.number ?? i)}
                />
              )}
              {o.description && <p className="answer-desc">{o.description}</p>}
              {o.preview && <pre className="answer-preview">{o.preview}</pre>}
            </div>
          ))}
        </Stack>
      ) : (
        <p className="answer-desc">
          {ask.kind === "raw"
            ? "Use the keys below or open the terminal to respond."
            : "No choices to pick from. Type an answer, or use the keys below."}
        </p>
      )}
      {ask.kind !== "raw" && (
        <TextInput
          mt="sm"
          placeholder="Type an answer"
          value={typed}
          onChange={(e) => setTyped(e.currentTarget.value)}
          aria-label="typed answer"
        />
      )}
      {error && (
        <p className="hire-error" role="alert">
          {error}
        </p>
      )}
      <div className="answer-actions">
        {ask.kind !== "raw" && (
          <button
            type="button"
            className="ab-btn hire-submit"
            onClick={submit}
            disabled={busy}
          >
            {busy ? "Sending…" : "Send answer"}
          </button>
        )}
        <button
          type="button"
          className="ab-btn board-mini"
          disabled={busy}
          onClick={() => void sendKey("esc")}
        >
          Cancel
        </button>
        {["up", "down", "left", "right", "enter"].map((k) => (
          <button
            key={k}
            type="button"
            className="ab-btn board-mini"
            disabled={busy}
            onClick={() => void sendKey(k)}
          >
            {k}
          </button>
        ))}
      </div>
    </div>
  );
}
