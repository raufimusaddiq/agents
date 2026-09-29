import { chromium } from "playwright-core";
import { AxeBuilder } from "@axe-core/playwright";
import { homedir } from "node:os";
import { join } from "node:path";

// System libs are extracted under ~/.local/lib/agent-board (no root needed);
// make them visible to the Chromium child process.
const LIBDIR = join(homedir(), ".local", "lib", "agent-board");
process.env.LD_LIBRARY_PATH = `${LIBDIR}:${process.env.LD_LIBRARY_PATH || ""}`;

const BASE = "http://127.0.0.1:8792";
const PW = "board-test-pw";

const browser = await chromium.launch({ headless: true });
async function login(page) {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  if (await page.getByLabel("board password").count()) {
    await page.getByLabel("board password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await page.locator('[aria-label^="column "]').first().waitFor({ timeout: 15000 });
}

let total = 0;
for (const theme of ["dark", "light"]) {
  for (const vp of [
    { width: 1280, height: 800, label: "desktop" },
    { width: 375, height: 720, label: "375" },
  ]) {
    const ctx = await browser.newContext({ viewport: vp });
    const page = await ctx.newPage();
    await login(page);
    if (theme === "light") {
      await page.getByTestId("theme-toggle").click();
      await page.waitForTimeout(400);
    }
    const res = await new AxeBuilder({ page }).analyze();
    const violations = res.violations;
    total += violations.length;
    console.log(`${theme}/${vp.label}: ${violations.length} axe violations`);
    for (const v of violations) {
      console.log(`   ${v.id} (${v.impact}): ${v.description}`);
    }
    await page.close();
    await ctx.close();
  }
}
await browser.close();
console.log(total === 0 ? "AXE CLEAN" : `AXE TOTAL ${total}`);
process.exit(total === 0 ? 0 : 1);
