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
      await api.answer(pane, answer);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
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
              {o.description && (
                <p className="answer-desc">{o.description}</p>
              )}
              {o.preview && (
                <pre className="answer-preview">{o.preview}</pre>
              )}
            </div>
          ))}
        </Stack>
      ) : (
        <p className="answer-desc">
          No choices to pick from. Type an answer, or use the keys below.
        </p>
      )}
      <TextInput
        mt="sm"
        placeholder="Type an answer"
        value={typed}
        onChange={(e) => setTyped(e.currentTarget.value)}
        aria-label="typed answer"
      />
      {error && <p className="hire-error">{error}</p>}
      <div className="answer-actions">
        <button
          type="button"
          className="ab-btn hire-submit"
          onClick={submit}
          disabled={busy}
        >
          {busy ? "Sending…" : "Send answer"}
        </button>
        <button
          type="button"
          className="ab-btn board-mini"
          onClick={() => api.keys(pane, ["esc"]).then(onDone)}
        >
          Cancel
        </button>
        {["up", "down", "left", "right", "enter"].map((k) => (
          <button
            key={k}
            type="button"
            className="ab-btn board-mini"
            onClick={() => api.keys(pane, [k]).then(onDone)}
          >
            {k}
          </button>
        ))}
      </div>
    </div>
  );
}
