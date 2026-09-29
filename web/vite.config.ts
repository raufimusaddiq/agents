import type { ClientRequest, IncomingMessage } from "node:http";
import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

function boardProxy(ws = false): ProxyOptions {
  return {
    target: "http://127.0.0.1:8792",
    changeOrigin: true,
    ws,
    configure(proxy) {
      const forwardOrigin = (
        request: ClientRequest,
        incoming: IncomingMessage,
      ) => {
        const origin = incoming.headers.origin;
        if (!origin) return;
        try {
          const parsed = new URL(origin);
          // Translate only the dev page's own origin. Foreign origins still
          // reach the backend unchanged and fail its allow-list check.
          if (
            ["http:", "https:"].includes(parsed.protocol) &&
            parsed.host === incoming.headers.host
          ) {
            request.setHeader("Origin", "http://127.0.0.1:8792");
          }
        } catch {
          /* Backend rejects malformed origins. */
        }
      };
      proxy.on("proxyReq", forwardOrigin);
      proxy.on("proxyReqWs", forwardOrigin);
    },
  };
}

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": boardProxy(),
      "/healthz": boardProxy(),
      "/ws": boardProxy(true),
    },
  },
});
