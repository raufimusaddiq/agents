import { chromium } from "playwright-core";

const BASE = process.env.BOARD_URL || "http://127.0.0.1:8792";
const PW = process.env.BOARD_PW || "board-test-pw";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

const errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByLabel("board password").fill(PW);
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByText("Agent Board").first().waitFor({ timeout: 10000 });

const columns = await page.locator('[aria-label^="column "]').count();
console.log("columns:", columns);
console.log("console errors:", errors.length ? errors : "none");
await page.screenshot({ path: "/tmp/opencode/board-dark.png", fullPage: true });
await browser.close();
console.log("SMOKE OK");
