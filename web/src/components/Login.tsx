import { useState } from "react";
import { PasswordInput, Stack } from "@mantine/core";
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
    <main className="login-shell">
      <div className="ab-panel login-card">
        <p className="login-marquee">
          <span className="ar-mark" aria-hidden>
            ▮
          </span>
          Staff only
        </p>
        <h1 className="font-display login-wordmark">
          Agent&nbsp;Board
        </h1>
        <p className="login-tag">
          The board that types into your agents. Log in to take the shift.
        </p>
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
          {error && <p className="hire-error">{error}</p>}
          <button
            type="button"
            className="ab-btn hire-submit login-go"
            onClick={submit}
            disabled={busy}
          >
            {busy ? "Checking…" : "Sign in"}
          </button>
        </Stack>
      </div>
    </main>
  );
}
