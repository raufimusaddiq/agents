import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Radio,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
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
    <Card
      withBorder
      padding="sm"
      style={{ borderColor: "var(--mantine-color-yellow-6)" }}
      aria-label={`needs you ${pane}`}
    >
      <Text fw={600} size="sm" mb={6}>
        Needs you
      </Text>
      {ask.tabs.length > 0 && (
        <Group gap={4} mb={6}>
          {ask.tabs.map((t) => (
            <Badge key={t} size="xs" variant="light">
              {t}
            </Badge>
          ))}
        </Group>
      )}
      <Text size="sm" mb={8} style={{ whiteSpace: "pre-wrap" }}>
        {ask.question}
      </Text>
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
                <Text size="xs" c="dimmed" ml={24}>
                  {o.description}
                </Text>
              )}
              {o.preview && (
                <Text
                  size="xs"
                  c="dimmed"
                  ml={24}
                  style={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}
                >
                  {o.preview}
                </Text>
              )}
            </div>
          ))}
        </Stack>
      ) : (
        <Text size="xs" c="dimmed" mb={6}>
          No numbered options. Use the arrow keys below, or type an answer.
        </Text>
      )}
      <TextInput
        mt="sm"
        placeholder="Type an answer (never guessed)"
        value={typed}
        onChange={(e) => setTyped(e.currentTarget.value)}
        aria-label="typed answer"
      />
      {error && (
        <Text c="red" size="xs" mt={4}>
          {error}
        </Text>
      )}
      <Group mt="sm" gap="xs">
        <Button size="xs" onClick={submit} loading={busy}>
          Answer
        </Button>
        <Button
          size="xs"
          variant="light"
          color="gray"
          onClick={() => api.keys(pane, ["esc"]).then(onDone)}
        >
          Esc
        </Button>
        {["up", "down", "left", "right", "enter"].map((k) => (
          <Button
            key={k}
            size="xs"
            variant="subtle"
            onClick={() => api.keys(pane, [k]).then(onDone)}
          >
            {k}
          </Button>
        ))}
      </Group>
    </Card>
  );
}
