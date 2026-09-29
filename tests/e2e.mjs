import { chromium } from "playwright-core";
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const BASE = process.env.BOARD_URL || "http://127.0.0.1:8792";
const PW = process.env.BOARD_PW || "board-test-pw";
const SCRATCH = join(homedir(), "board-e2e-scratch");
const KIND = process.env.E2E_KIND || "opencode";
const UNIQUE = `e2e${KIND}${Date.now().toString(36)}`;

let passed = 0;
let failed = 0;
const results = [];
function check(name, cond, detail = "") {
  if (cond) {
    passed++;
    results.push(`  PASS ${name}`);
  } else {
    failed++;
    results.push(`  FAIL ${name} ${detail}`);
  }
}

function sh(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { encoding: "utf8", ...opts });
}

function gitInit() {
  rmSync(SCRATCH, { recursive: true, force: true });
  mkdirSync(SCRATCH, { recursive: true });
  sh("git", ["-C", SCRATCH, "init", "-q", "-b", "main"]);
  sh("git", ["-C", SCRATCH, "config", "user.email", "e2e@test.co"]);
  sh("git", ["-C", SCRATCH, "config", "user.name", "e2e"]);
}

async function login(page) {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1000);
  if (await page.getByLabel("board password").count()) {
    await page.getByLabel("board password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await page.locator('[aria-label^="column "]').first().waitFor({ timeout: 15000 });
}

async function apiLogin() {
  const res = await fetch(`${BASE}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: PW }),
  });
  const cookie = res.headers.get("set-cookie") || "";
  return cookie.split(";")[0];
}

async function answerAsk(page, name) {
  // Target ONLY the fixture's card by its unique aria-label.
  const card = page.locator(`[aria-label="agent ${name}"]`);
  if (!(await card.count())) return false;
  await card.click();
  const panel = page.locator(`[aria-label^="agent panel ${name}"]`);
  await panel.waitFor({ timeout: 8000 });
  const answerBtn = panel.getByRole("button", { name: "Answer" });
  if (!(await answerBtn.count())) return false;
  // pick first radio if present
  const radio = panel.getByRole("radio").first();
  if (await radio.count()) await radio.check();
  await answerBtn.click();
  return true;
}

(async () => {
  gitInit();
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await login(page);

  // ---- hire the fixture ----
  const hireBtn = page.getByTestId("hire-open");
  await hireBtn.click();
  await page.getByRole("combobox", { name: "Harness", exact: true }).click();
  await page.getByRole("option", { name: KIND }).click();
  await page.getByLabel("hire folder").fill(SCRATCH);
  await page.getByLabel("hire name").fill(UNIQUE);
  await page.getByTestId("hire-submit").click();
  // wait for the card to appear; reload periodically in case SSE lagged
  const card = page.locator(`[aria-label="agent ${UNIQUE}"]`);
  let hired = false;
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(4000);
    if (await card.count()) {
      hired = true;
      break;
    }
    // nudge the board in case the SSE event was missed
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
  }
  check("hire: agent chip appears", hired, hired ? "" : "(timeout)");

  // ---- answer any startup prompt (folder trust / hooks etc.) ----
  if (hired) {
    for (let i = 0; i < 8; i++) {
      const answered = await answerAsk(page, UNIQUE);
      if (!answered) break;
      await page.waitForTimeout(4000);
    }
    await page.waitForTimeout(3000);
  }

  // ---- chat prompt + reply ----
  if (await card.count()) {
    await card.click();
    const panel = page.locator(`[aria-label^="agent panel ${UNIQUE}"]`);
    await panel.getByLabel("message agent").fill("Reply with exactly: E2E_PONG");
    await panel.getByRole("button", { name: "Send" }).click();
    await page.waitForTimeout(15000);
    const txt = await panel.innerText();
    check("chat: reply visible", txt.includes("E2E_PONG"), txt.slice(0, 80));
    // tabs present
    check("panel: Code changes tab", txt.includes("Code changes"));
    check("panel: Workflow tab", txt.includes("Workflow"));
    check("panel: Safety tab", txt.includes("Safety"));
  }

  // ---- make it commit ----
  const cookie = await apiLogin();
  const board = await (await fetch(`${BASE}/api/board`, {
    headers: { Cookie: cookie },
  })).json();
  const myCard = board.cards.find((c) => c.name === UNIQUE);
  if (myCard) {
    await fetch(`${BASE}/api/prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: cookie },
      body: JSON.stringify({
        pane: myCard.pane,
        text: "Create x_e2e.py with print(1). Then run: git add -A && git commit -m e2e. Say E2E_COMMITTED.",
      }),
    });
    // The harness may ask permission for the git command; answer any prompt
    // from the fixture's own card (targeted by the unique name) until the
    // commit lands or we run out of attempts.
    let log = "";
    for (let i = 0; i < 12; i++) {
      await page.waitForTimeout(5000);
      await answerAsk(page, UNIQUE);
      try {
        log = sh("git", ["-C", SCRATCH, "log", "--oneline"]);
      } catch {
        log = "";
      }
      if (log.includes("e2e")) break;
    }
    check("commit happened", log.includes("e2e"), log.slice(0, 60));
    await page.waitForTimeout(12000);
    const b2 = await (await fetch(`${BASE}/api/board`, {
      headers: { Cookie: cookie },
    })).json();
    check("workflow alert fired", b2.alerts.some((a) => a.pane === myCard.pane));
  }

  // ---- security checks ----
  const noauth = await fetch(`${BASE}/api/board`);
  check("security: no session -> 403", noauth.status === 403, String(noauth.status));

  const badOrigin = await fetch(`${BASE}/api/board`, {
    headers: { Cookie: cookie, Origin: "http://evil.example" },
  });
  check("security: bad Origin -> 403", badOrigin.status === 403, String(badOrigin.status));

  // machine token create + use + revoke
  const tokRes = await (
    await fetch(`${BASE}/api/machine_token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: cookie },
      body: JSON.stringify({ action: "create", name: UNIQUE }),
    })
  ).json();
  const mt = tokRes.token;
  check("security: machine token issued", !!mt);
  const useTok = await fetch(`${BASE}/api/board`, { headers: { "X-Board-Token": mt } });
  check("security: machine token works", useTok.status === 200, String(useTok.status));
  await fetch(`${BASE}/api/machine_token`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Cookie: cookie },
    body: JSON.stringify({ action: "revoke", name: UNIQUE }),
  });
  const revoked = await fetch(`${BASE}/api/board`, { headers: { "X-Board-Token": mt } });
  check("security: revoked token -> 403", revoked.status === 403, String(revoked.status));

  // audit log records the prompt, not its text
  let audit = "";
  try {
    audit = sh("bash", ["-c", `tail -50 ${join(homedir(), "agents", "audit.jsonl")}`]);
  } catch {
    /* empty */
  }
  check("audit: records prompt action", audit.includes('"prompt"'), "");
  check("audit: never logs prompt text", !audit.includes("E2E_PONG"), "");

  // lockout
  for (let i = 0; i < 11; i++) {
    await fetch(`${BASE}/api/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: "wrong-" + i }),
    });
  }
  const locked = await fetch(`${BASE}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: "wrong-final" }),
  });
  check("security: lockout -> 429", locked.status === 429, String(locked.status));

  // Clear the lockout so the test never leaves the real user blocked. The
  // server only allows this from localhost.
  await fetch(`${BASE}/api/dev/reset_lockout`, { method: "POST" });
  const afterReset = await fetch(`${BASE}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: PW }),
  });
  check("lockout clears after reset", afterReset.status === 200, String(afterReset.status));

  await browser.close();

  // ---- cleanup: close fixture panes/folders ----
  try {
    rmSync(SCRATCH, { recursive: true, force: true });
  } catch {
    /* ignore */
  }

  console.log(results.join("\n"));
  console.log(`\nE2E ${KIND}: ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
