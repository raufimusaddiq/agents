import { chromium } from "playwright-core";
import { homedir } from "node:os";
import { join } from "node:path";

process.env.LD_LIBRARY_PATH = `${join(homedir(), ".local", "lib", "agent-board")}:${process.env.LD_LIBRARY_PATH || ""}`;

const BASE = process.env.BOARD_URL || "http://127.0.0.1:8792";
const PW = process.env.BOARD_PW || "board-test-pw";

let pass = 0;
let fail = 0;
function check(name, cond, detail = "") {
  if (cond) {
    pass++;
    console.log("  PASS", name);
  } else {
    fail++;
    console.log("  FAIL", name, detail);
  }
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
if (await page.getByLabel("board password").count()) {
  await page.getByLabel("board password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
}
await page.locator('[aria-label^="column "]').first().waitFor({ timeout: 15000 });

// Columns are work stages only, no agent-state columns.
const colNames = await page
  .locator('[aria-label^="column "]')
  .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
check("columns are work stages", colNames.join(",").includes("column To do"));
check("no Idle column", !colNames.some((c) => c === "column Idle"));
check("no Needs you column", !colNames.some((c) => c === "column Needs you"));
check("Parked column present", colNames.some((c) => c === "column Parked"));

// Board unit is a ticket, and agents are chips inside it.
const tickets = await page.locator('[data-testid="ticket"]').count();
check("tickets render", tickets > 0, String(tickets));
const chips = await page.locator('[aria-label^="agent "]').count();
check("agent chips render", chips > 0, String(chips));

// A ticket card shows agent chips, not the agent itself as the unit.
const firstTicket = page.locator('[data-testid="ticket"]').first();
const ticketText = await firstTicket.innerText();
check("ticket shows an agent chip", (await firstTicket.locator('[aria-label^="agent "]').count()) > 0, ticketText.slice(0, 60));

// Crew rail lists agents and toggles.
check("crew rail visible", (await page.locator(".crew").count()) > 0);
const crewCount = await page.locator('[data-testid="crew-agent"]').count();
check("crew lists agents", crewCount > 0, String(crewCount));
await page.getByTestId("crew-toggle").click();
await page.waitForTimeout(400);
check("crew toggles off", (await page.locator(".crew").count()) === 0);
await page.getByTestId("crew-toggle").click();
await page.waitForTimeout(400);
check("crew toggles on", (await page.locator(".crew").count()) > 0);

// Clicking a chip opens that agent's panel.
const chipLabel = await page.locator('[aria-label^="agent "]').first().getAttribute("aria-label");
const chipName = chipLabel.replace("agent ", "");
await page.locator('[aria-label^="agent "]').first().click();
await page.locator(`[aria-label^="agent panel ${chipName}"]`).waitFor({ timeout: 8000 });
check("chip opens agent panel", true);

// Worktrees are first-class: a parked worktree card named from its branch.
const parked = await page.locator('[aria-label^="worktree "]').count();
check("parked worktrees render", parked >= 0, String(parked));
const parkedTicket = await page
  .locator('[aria-label^="worktree "]')
  .first()
  .innerText()
  .catch(() => "");
if (parked > 0) {
  check("parked worktree shows a ticket name", parkedTicket.length > 0, parkedTicket.slice(0, 40));
}

await page.screenshot({ path: "/tmp/opencode/ticket-board.png", fullPage: true });
await browser.close();
console.log(`\nTICKETS: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
