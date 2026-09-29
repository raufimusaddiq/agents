import { useState } from "react";
import { Button, Card, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { api } from "../api";

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [pw, setPw] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.login(pw);
      onSuccess();
    } catch (e) {
      setError(
        e instanceof Error && e.message === "locked_out"
          ? "Too many attempts. Try again in 15 minutes."
          : "Wrong password.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        height: "100vh",
      }}
    >
      <Card withBorder padding="lg" w={340}>
        <Title order={1} mb="sm" size="h3">
          Agent Board
        </Title>
        <Stack gap="sm">
          <PasswordInput
            label="Password"
            value={pw}
            onChange={(e) => setPw(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            aria-label="board password"
          />
          {error && (
            <Text c="red" size="xs">
              {error}
            </Text>
          )}
          <Button onClick={submit} loading={busy}>
            Sign in
          </Button>
        </Stack>
      </Card>
    </main>
  );
}
