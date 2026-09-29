import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider, Modal, useMantineColorScheme } from "@mantine/core";
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
    o.onended = () => {
      void ctx.close();
    };
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
  const [loadError, setLoadError] = useState("");
  const loading = useRef(false);
  const authGeneration = useRef(0);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const pendingRefresh = useRef(false);
  const [connectionState, setConnectionState] = useState<
    "connecting" | "live" | "reconnecting"
  >("connecting");

  const load = useCallback(async function refresh() {
    if (loading.current) {
      pendingRefresh.current = true;
      return;
    }
    loading.current = true;
    const generation = authGeneration.current;
    try {
      const b = await api.board();
      if (generation !== authGeneration.current) return;
      setLoadError("");
      setBoard(b);
      setAuthed(true);
      // Needs-you detection: chime + browser notification + tab title count.
      const needs = b.agents.filter((a) => a.needs_user);
      const ids = new Set<string>(needs.map((a) => a.pane));
      for (const a of needs) {
        if (!needsRef.current.has(a.pane)) {
          chime();
          if (
            "Notification" in window &&
            Notification.permission === "granted"
          ) {
            new Notification(`Needs you: ${a.name}`, {
              body: "An agent is waiting for your input. Open the board to respond.",
            });
          }
        }
      }
      needsRef.current = ids;
      document.title = needs.length
        ? `(${needs.length}) Agent Board`
        : "Agent Board";
    } catch (e) {
      if (generation !== authGeneration.current) return;
      if (e instanceof Error && e.message === "forbidden") {
        setAuthed(false);
        setBoard(null);
        setSelected(null);
      } else {
        setLoadError("Unable to reach the board. Try again.");
      }
    } finally {
      loading.current = false;
      if (pendingRefresh.current) {
        pendingRefresh.current = false;
        void refresh();
      }
    }
  }, []);

  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true);
    try {
      await api.logout();
      authGeneration.current += 1;
      pendingRefresh.current = false;
      setAuthed(false);
      setBoard(null);
      setSelected(null);
      setLoadError("");
      needsRef.current.clear();
      document.title = "Agent Board";
    } catch {
      setLoadError("Unable to sign out. Try again.");
    } finally {
      setLogoutBusy(false);
    }
  }

  useEffect(() => {
    load();
    const vis = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", vis);
    return () => document.removeEventListener("visibilitychange", vis);
  }, [load]);

  useEffect(() => {
    if (!authed) return;
    const es = new EventSource("/api/events");
    es.onopen = () => {
      setConnectionState("live");
      void load();
    };
    es.onmessage = () => {
      load();
    };
    es.onerror = () => {
      setConnectionState("reconnecting");
      void load();
    };
    return () => {
      es.close();
    };
  }, [authed, load]);

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
    return (
      <main className="app-status ab-panel">
        <p role="status">{loadError || "Loading…"}</p>
        {loadError && (
          <button className="ab-btn" onClick={() => void load()}>
            Retry
          </button>
        )}
      </main>
    );
  }

  return (
    <main className="app-shell">
      <h1 className="visually-hidden">Agent Board</h1>
      <div className="app-board">
        {board.read_only && (
          <p className="app-notice">
            Read-only preview. Live agent data; agent actions are disabled.
          </p>
        )}
        {loadError && (
          <p className="app-notice" role="alert">
            {loadError}{" "}
            <button className="ab-btn board-mini" onClick={() => void load()}>
              Retry
            </button>
          </p>
        )}
        {"Notification" in window && Notification.permission === "default" && (
          <button
            className="ab-btn board-mini"
            onClick={() => {
              void Notification.requestPermission();
            }}
          >
            Enable notifications
          </button>
        )}
        <BoardView
          board={board}
          connectionState={connectionState}
          onLogout={() => void logout()}
          logoutBusy={logoutBusy}
          theme={colorScheme}
          onToggleTheme={() =>
            setColorScheme(colorScheme === "dark" ? "light" : "dark")
          }
          selected={selected}
          onSelect={setSelected}
        />
      </div>
      {selected && (
        <div className="app-panel">
          <AgentPanel
            key={selected}
            pane={selected}
            readOnly={!!board.read_only}
            onClose={() => setSelected(null)}
          />
        </div>
      )}
    </main>
  );
}

export default function Root() {
  return (
    <MantineProvider
      defaultColorScheme="dark"
      theme={{
        components: {
          Modal: Modal.extend({
            defaultProps: {
              closeButtonProps: { "aria-label": "Close dialog" },
            },
          }),
        },
        fontFamily: "Space Grotesk, ui-sans-serif, system-ui, sans-serif",
        headings: {
          fontFamily: "Bungee, Arial Black, system-ui, sans-serif",
          fontWeight: "400",
        },
        defaultRadius: 0,
        radius: { xs: "0px", sm: "0px", md: "0px", lg: "0px", xl: "0px" },
        primaryColor: "board",
        primaryShade: { light: 6, dark: 5 },
        colors: {
          board: [
            "#e4f5ee",
            "#c4e7da",
            "#9cd6c2",
            "#70c0a7",
            "#48a68c",
            "#2e7d6f",
            "#256b5f",
            "#1b554b",
            "#123f37",
            "#092922",
          ],
          // Retro control-room signals. Shade 5/6 used for filled controls so
          // white text clears WCAG AA on the light theme.
          signal: [
            "#ffd9c9",
            "#ffb69c",
            "#f88f6e",
            "#ef6a45",
            "#e4572e",
            "#c8481f",
            "#a53a19",
            "#822d13",
            "#5f210e",
            "#3d1509",
          ],
        },
      }}
    >
      <App />
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Root />);
