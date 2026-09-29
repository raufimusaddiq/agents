import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider, useMantineColorScheme } from "@mantine/core";
import "@mantine/core/styles.css";
import "./index.css";
import type { Board } from "./types";
import { api } from "./api";
import { BoardView } from "./components/Board";
import { AgentPanel } from "./components/AgentPanel";
import { Login } from "./components/Login";

function chime() {
  try {
    const ctx = new AudioContext();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g);
    g.connect(ctx.destination);
    o.frequency.value = 880;
    g.gain.setValueAtTime(0.1, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    o.start();
    o.stop(ctx.currentTime + 0.3);
  } catch {
    /* audio may be blocked until user gesture; ignore */
  }
}

function App() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const needsRef = useRef<Set<string>>(new Set());
  const awayRef = useRef<number>(Date.now());

  const load = useCallback(async () => {
    try {
      const b = await api.board();
      setBoard(b);
      setAuthed(true);
      // Needs-you detection: chime + browser notification + tab title count.
      const needs = b.cards.filter((c) => c.needs_user);
      const ids = new Set(needs.map((c) => c.pane));
      for (const c of needs) {
        if (!needsRef.current.has(c.pane)) {
          chime();
          if ("Notification" in window && Notification.permission === "granted") {
            new Notification(`Needs you: ${c.name}`, { body: c.last_line });
          }
        }
      }
      needsRef.current = ids;
      document.title = needs.length
        ? `(${needs.length}) Agent Board`
        : "Agent Board";
    } catch (e) {
      if (e instanceof Error && e.message === "forbidden") setAuthed(false);
    }
  }, []);

  useEffect(() => {
    load();
    const es = new EventSource("/api/events");
    es.onmessage = () => {
      awayRef.current = Date.now();
      load();
    };
    es.onerror = () => {
      /* browser auto-reconnects SSE */
    };
    const vis = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", vis);
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
    return () => {
      es.close();
      document.removeEventListener("visibilitychange", vis);
    };
  }, [load]);

  if (authed === false) {
    return (
      <Login
        onSuccess={() => {
          setAuthed(true);
          load();
        }}
      />
    );
  }
  if (!board) {
    return <div style={{ padding: 24 }}>Loading…</div>;
  }

  return (
    <main
      style={{
        display: "flex",
        gap: 12,
        padding: 12,
        height: "100vh",
        boxSizing: "border-box",
      }}
    >
      <h1
        style={{ position: "absolute", left: -9999, width: 1, height: 1, overflow: "hidden" }}
      >
        Agent Board
      </h1>
      <div style={{ flex: selected ? "1 1 60%" : "1 1 100%", minWidth: 0 }}>
        <BoardView
          board={board}
          theme={colorScheme}
          onToggleTheme={() =>
            setColorScheme(colorScheme === "dark" ? "light" : "dark")
          }
          selected={selected}
          onSelect={setSelected}
        />
      </div>
      {selected && (
        <div style={{ flex: "1 1 40%", minWidth: 320 }}>
          <AgentPanel pane={selected} onClose={() => setSelected(null)} />
        </div>
      )}
    </main>
  );
}

export default function Root() {
  return (
    <MantineProvider defaultColorScheme="dark" theme={{ primaryShade: { light: 8, dark: 7 } }}>
      <App />
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Root />);
