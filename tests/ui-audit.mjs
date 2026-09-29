import { homedir } from "node:os";
import { join, resolve, extname } from "node:path";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { AxeBuilder } from "@axe-core/playwright";
process.env.LD_LIBRARY_PATH = `${join(homedir(), ".local/lib/agent-board")}:${process.env.LD_LIBRARY_PATH || ""}`;
const browser = await chromium.launch({ headless: true });
const dist = resolve(
  process.env.AUDIT_DIST ||
    fileURLToPath(new URL("../web/dist", import.meta.url)),
);
let staticServer;
let base = process.env.AUDIT_BASE;
if (!base) {
  staticServer = createServer(async (request, response) => {
    const path = resolve(
      dist,
      "." + new URL(request.url, "http://localhost").pathname,
    );
    try {
      if (!path.startsWith(dist + "/") && path !== dist)
        throw new Error("bad path");
      const file = path === dist ? join(dist, "index.html") : path;
      const data = await readFile(file);
      response.setHeader(
        "Content-Type",
        {
          ".html": "text/html",
          ".js": "text/javascript",
          ".css": "text/css",
          ".woff2": "font/woff2",
        }[extname(file)] || "application/octet-stream",
      );
      response.end(data);
    } catch {
      response.writeHead(404);
      response.end("Build the frontend before running the UI audit.");
    }
  });
  await new Promise((resolve) => staticServer.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${staticServer.address().port}`;
}
try {
  for (const width of [1280, 375]) {
    const context = await browser.newContext({
      viewport: { width, height: 800 },
    });
    const page = await context.newPage();
    let mode = "offline",
      logins = 0,
      failTyping = false,
      failAgent = false;
    const sentKeys = [],
      typed = [];
    const columns = [
      "To do",
      "In progress",
      "Testing",
      "Review",
      "Ready to push",
      "Shipped",
      "Parked",
    ];
    const agent = {
      pane: "w1:p1",
      name: "Audit agent",
      kind: "codex",
      cwd: "/tmp/audit",
      repo: "/tmp/audit",
      git: {
        repo: "/tmp/audit",
        branch: "audit",
        upstream: "origin/audit",
        ahead: 0,
        behind: 0,
        files: [],
        files_capped: false,
        commits: [
          {
            sha: "abcdef1234567",
            short: "abcdef1",
            subject: "Audit fixture",
            date: "2026-09-30",
            pushed: true,
          },
        ],
        unpushed_commits: 0,
        has_remote: true,
      },
      chat: [
        { kind: "prompt", text: "Check the project." },
        { kind: "reply", text: "Checking live feedback." },
      ],
      alerts: [],
      supports: {},
      notes: [],
      ask: null,
      screen_tail: ["Ready for input"],
      status: { model: "fixture" },
      usage_history: [],
    };
    const chip = {
      pane: agent.pane,
      name: agent.name,
      kind: agent.kind,
      agent_status: "idle",
      needs_user: false,
      stage: "Working",
      last_line: "Ready",
      context_pct: null,
      tab_label: "Audit",
      ws_label: "Audit",
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/logout") {
        mode = "login";
        return route.fulfill({ json: { ok: true } });
      }
      if (path === "/api/login") {
        logins++;
        mode = "board";
        return route.fulfill({ json: { ok: true } });
      }
      if (path === "/api/workspaces")
        return route.fulfill({ json: { workspaces: [] } });
      if (path === "/api/folders")
        return route.fulfill({
          json: {
            path: "/tmp/audit",
            home: "/tmp",
            parent: "/tmp",
            can_descend: true,
            dirs: [],
          },
        });
      if (path === "/api/agent")
        return route.fulfill(
          failAgent
            ? { status: 503, json: { error: "unavailable" } }
            : { json: agent },
        );
      if (path === "/api/type") {
        typed.push(route.request().postDataJSON().text);
        return route.fulfill(
          failTyping
            ? { status: 409, json: { error: "agent_not_ready" } }
            : { json: { ok: true } },
        );
      }
      if (path === "/api/keys") {
        sentKeys.push(...route.request().postDataJSON().keys);
        return route.fulfill({ json: { ok: true } });
      }
      if (path === "/api/menu")
        return route.fulfill({ json: { items: [], kind: "codex" } });
      if (path === "/api/fire")
        return route.fulfill({ status: 409, json: { error: "pane_busy" } });
      if (path === "/api/events")
        return route.fulfill({
          contentType: "text/event-stream",
          body: ": connected\n\n",
        });
      if (mode === "offline")
        return route.fulfill({ status: 503, json: { error: "unavailable" } });
      if (mode === "login")
        return route.fulfill({ status: 403, json: { error: "forbidden" } });
      return route.fulfill({
        json: {
          tickets: [
            {
              id: "AUD-101",
              name: "AUD-101 — Audit project consistency",
              source: "branch ticket",
              agents: [chip],
              repo: "/tmp/audit",
              branch: "audit/ui-consistency",
              worktree: "agent/audit",
              stage: "In progress",
              unpushed: 2,
              needs_you_count: 0,
              context_pct_max: 42,
            },
          ],
          agents: [chip],
          stages: [],
          alerts: [],
          closed: [],
          frozen_roster: null,
          worktrees: [],
          remote_on: false,
          columns,
          now: Date.now() / 1000,
        },
      });
    });
    await page.goto(base);
    await page.getByRole("button", { name: "Retry" }).waitFor();
    mode = "login";
    await page.getByRole("button", { name: "Retry" }).click();
    await page.getByLabel("board password").fill("test");
    for (const theme of ["dark", "light"]) {
      await page.evaluate((theme) => {
        localStorage.setItem("mantine-color-scheme-value", theme);
        document.documentElement.setAttribute(
          "data-mantine-color-scheme",
          theme,
        );
      }, theme);
      const result = await new AxeBuilder({ page }).analyze();
      assert.equal(
        result.violations.length,
        0,
        JSON.stringify(
          result.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => n.target),
          })),
        ),
      );
    }
    await page.getByLabel("board password").press("Enter");
    await page.locator(".board-columns").waitFor();
    assert.equal(logins, 1);
    for (let i = 0; i < 2; i++) {
      const result = await new AxeBuilder({ page }).analyze();
      assert.equal(
        result.violations.length,
        0,
        JSON.stringify(
          result.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => n.target),
          })),
        ),
      );
      await page.getByTestId("theme-toggle").click();
    }
    if (
      (await page.getByTestId("crew-toggle").getAttribute("aria-pressed")) ===
      "false"
    )
      await page.getByTestId("crew-toggle").click();
    await page.getByRole("button", { name: "crew agent Audit agent" }).click();
    const composer = page.getByLabel("message agent");
    await composer.waitFor();
    for (let i = 0; i < 2; i++) {
      const result = await new AxeBuilder({ page }).analyze();
      assert.equal(
        result.violations.length,
        0,
        JSON.stringify(
          result.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => ({
              target: n.target,
              detail: n.failureSummary,
            })),
          })),
        ),
      );
      await page.getByTestId("theme-toggle").click();
    }
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
      "page must not overflow horizontally",
    );
    await page.screenshot({
      path: `/tmp/agent-board-audit-${width}.png`,
      fullPage: true,
    });
    await composer.fill("hello 😀");
    await composer.fill("hello ");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.waitForFunction(
      () =>
        document.querySelector('[aria-label="message agent"]')?.value === "",
    );
    assert.equal(
      sentKeys.filter((k) => k === "backspace").length,
      1,
      "one emoji deletion must use one backspace",
    );
    assert.equal(sentKeys.filter((k) => k === "enter").length, 1);
    await composer.fill("draft survives refresh");
    await page.getByRole("button", { name: "close agent panel" }).click();
    await page
      .getByRole("button", { name: "agent Audit agent", exact: true })
      .click();
    await composer.waitFor();
    assert.equal(
      await composer.inputValue(),
      "draft survives refresh",
      "closing and reopening must retain the native draft",
    );
    failAgent = true;
    await page
      .getByRole("alert")
      .filter({ hasText: "Unable to refresh this agent" })
      .waitFor({ timeout: 7000 });
    assert.equal(await composer.inputValue(), "draft survives refresh");
    failAgent = false;
    await page.getByRole("button", { name: "Retry" }).click();
    await page
      .getByRole("alert")
      .filter({ hasText: "Unable to refresh this agent" })
      .waitFor({ state: "hidden" });
    await page.getByTestId("hire-open").click();
    await page.getByLabel("hire name").fill("audit");
    await page.waitForTimeout(300);
    await page.screenshot({
      path: `/tmp/agent-board-audit-modal-${width}.png`,
      fullPage: true,
    });
    for (let i = 0; i < 2; i++) {
      const result = await new AxeBuilder({ page }).analyze();
      assert.equal(
        result.violations.length,
        0,
        JSON.stringify(
          result.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => ({
              target: n.target,
              detail: n.failureSummary,
            })),
          })),
        ),
      );
      const style = await page
        .locator(".mantine-Modal-content")
        .evaluate((el) => {
          const computed = getComputedStyle(el);
          const probe = document.createElement("span");
          probe.style.backgroundColor = "var(--ab-panel)";
          document.body.append(probe);
          const expected = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return {
            background: computed.backgroundColor,
            expected,
            border: computed.borderTopWidth,
            radius: computed.borderRadius,
          };
        });
      assert.equal(style.background, style.expected);
      assert.equal(style.border, "2px");
      assert.equal(style.radius, "0px");
      await page.screenshot({
        path: `/tmp/agent-board-audit-hire-${width}-${i}.png`,
        fullPage: true,
      });
      // The modal traps interaction; update the persisted theme through the page.
      await page.evaluate(() => {
        const root = document.documentElement;
        root.setAttribute(
          "data-mantine-color-scheme",
          root.getAttribute("data-mantine-color-scheme") === "dark"
            ? "light"
            : "dark",
        );
      });
    }
    await page.getByRole("button", { name: "Close dialog" }).click();
    await page.getByRole("button", { name: "Fire agent", exact: true }).click();
    await page.getByRole("button", { name: "Fire", exact: true }).click();
    await page.getByText("pane_busy", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    const entersBefore = sentKeys.filter((k) => k === "enter").length;
    failTyping = true;
    await composer.fill("cannot synchronize");
    await page
      .getByRole("alert")
      .filter({ hasText: "Input could not be synchronized" })
      .waitFor();
    assert.equal(await composer.isDisabled(), true);
    assert.equal(
      await page
        .getByRole("button", { name: "Send", exact: true })
        .isDisabled(),
      true,
    );
    assert.equal(
      sentKeys.filter((k) => k === "enter").length,
      entersBefore,
      "failed input must never submit",
    );
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.getByLabel("board password").waitFor();
    console.log(
      `PASS ${width}px: retry, login, board, accessibility in both themes, Unicode edits, draft retention, fire errors, failed-input protection`,
    );
    await context.close();
  }
} finally {
  await browser.close();
  if (staticServer) await new Promise((resolve) => staticServer.close(resolve));
}
