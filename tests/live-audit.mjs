// Read-only integration audit. Run against tests/live_server.py or BOARD_URL.
import assert from "node:assert/strict";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { AxeBuilder } from "@axe-core/playwright";
process.env.LD_LIBRARY_PATH = `${join(homedir(), ".local/lib/agent-board")}:${process.env.LD_LIBRARY_PATH || ""}`;
const base = process.env.BOARD_URL || "http://127.0.0.1:8793";
const login = await fetch(`${base}/api/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ password: process.env.BOARD_PW || "board-test-pw" }),
});
assert.equal(login.status, 200, "provide the board password through BOARD_PW");
const cookie = login.headers.get("set-cookie").split(";")[0];
const headers = { Cookie: cookie };
const started = performance.now();
const response = await fetch(`${base}/api/board`, { headers });
assert.equal(response.status, 200);
const board = await response.json();
if (board.read_only) {
  const denied = await fetch(`${base}/api/type`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(denied.status, 403);
  assert.equal((await denied.json()).error, "read_only_preview");
}
console.log(
  `Live board: ${board.agents.length} agents, ${board.tickets.length} tickets, ${(performance.now() - started).toFixed(0)}ms`,
);
assert.ok(
  board.agents.length > 0,
  "live integration requires an existing agent",
);
for (const agent of board.agents) {
  const response = await fetch(
    `${base}/api/agent?pane=${encodeURIComponent(agent.pane)}`,
    { headers },
  );
  assert.equal(response.status, 200);
  const detail = await response.json();
  assert.equal(detail.pane, agent.pane);
  assert.ok(Array.isArray(detail.chat));
  assert.ok(Array.isArray(detail.screen_tail));
  console.log(
    `Agent ${agent.name}: ${agent.kind}, ${agent.agent_status}, ${detail.chat.length} chat rows, ${detail.screen_tail.length} screen lines, needs user ${agent.needs_user}`,
  );
}
const controller = new AbortController();
let streamChanges = 0;
const streamCheck = (async () => {
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(`${base}/api/events`, {
      headers,
      signal: controller.signal,
    });
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /text\/event-stream/);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      streamChanges += (text.match(/data: /g) || []).length;
    }
  } catch (e) {
    if (e.name !== "AbortError") throw e;
  } finally {
    clearTimeout(timer);
  }
})();
const browser = await chromium.launch({ headless: true });
try {
  const representatives = [
    ...new Map(board.agents.map((agent) => [agent.kind, agent])).values(),
  ];
  for (const width of [1280, 375]) {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
    });
    await context.addCookies([
      {
        name: "board_session",
        value: cookie.slice("board_session=".length),
        url: base,
        httpOnly: true,
        sameSite: "Strict",
      },
    ]);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base);
    await page.locator(".board-columns").waitFor();
    await page
      .locator(".board-feedback")
      .filter({ hasText: "Live" })
      .waitFor({ timeout: 15000 });
    for (const target of representatives) {
      await page
        .getByRole("button", { name: `agent ${target.name}`, exact: true })
        .first()
        .click();
      await page.getByLabel("message agent").waitFor();
      if (board.read_only)
        assert.ok(await page.getByLabel("message agent").isDisabled());
      for (let theme = 0; theme < 2; theme++) {
        for (const tab of ["Chat", "Code changes", "Workflow", "Safety"]) {
          await page.getByRole("tab", { name: tab, exact: true }).click();
          const result = await new AxeBuilder({ page }).analyze();
          assert.equal(
            result.violations.length,
            0,
            JSON.stringify(
              result.violations.map((v) => ({
                id: v.id,
                targets: v.nodes.map((n) => n.target),
              })),
            ),
          );
          assert.equal(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= window.innerWidth,
            ),
            true,
            "no page overflow",
          );
        }
        await page.getByTestId("theme-toggle").click();
      }
      console.log(`PASS live ${target.kind} tabs at ${width}px in both themes`);
    }
    assert.deepEqual(errors, []);
    await page.screenshot({
      path: `/tmp/agent-board-audit-live-${width}.png`,
      fullPage: true,
    });
    await context.close();
  }
  console.log("PASS live connection indicator and no browser exceptions");
} finally {
  await browser.close();
}
await streamCheck;
assert.ok(
  streamChanges > 0,
  "expected feedback from the active live agent during the observation window",
);
console.log(
  `PASS live feedback stream: ${streamChanges} change notifications in 12 seconds`,
);
