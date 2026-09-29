import { useEffect, useRef, useState } from "react";
import { Modal } from "@mantine/core";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

/**
 * The full herdr TUI in the browser. The server spawns a fresh herdr client in
 * a pty and bridges it over a WebSocket; closing only detaches, so the herdr
 * server and every agent keep running.
 */
export function TerminalModal({
  pane,
  name,
  opened,
  onClose,
}: {
  pane: string | null;
  name: string;
  opened: boolean;
  onClose: () => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [hostReady, setHostReady] = useState(false);

  useEffect(() => {
    if (!opened || !pane || !hostReady || !hostRef.current) return;
    let disposed = false;
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 13,
      theme: { background: "#101014", foreground: "#f4ead2" },
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url =
      `${proto}//${location.host}/ws/terminal?pane=${encodeURIComponent(pane)}` +
      `&cols=${term.cols}&rows=${term.rows}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";

    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    };

    ws.onopen = () => {
      try {
        fit.fit();
      } catch {
        /* host may be hidden */
      }
      sendResize();
      term.focus();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") term.write(ev.data);
      else term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => {
      term.write("\r\n\x1b[33m[detached — agents keep running]\x1b[0m\r\n");
    };
    const dataSub = term.onData((d) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(new TextEncoder().encode(d));
    });
    const resizeSub = term.onResize(sendResize);
    const onWinResize = () => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("resize", onWinResize);
    const raf = requestAnimationFrame(onWinResize);

    return () => {
      disposed = true;
      void disposed;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onWinResize);
      dataSub.dispose();
      resizeSub.dispose();
      ws.close();
      term.dispose();
    };
  }, [opened, pane, hostReady]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      fullScreen
      title={
        <span className="font-display terminal-title">
          Terminal · {name}
        </span>
      }
    >
      <div
        className="terminal-host"
        ref={(el) => {
          hostRef.current = el;
          setHostReady(!!el);
        }}
      />
    </Modal>
  );
}
