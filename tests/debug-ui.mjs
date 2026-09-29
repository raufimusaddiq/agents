import { chromium } from "playwright-core";

const BASE = "http://127.0.0.1:8792";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

page.on("console", (m) => console.log("CONSOLE", m.type(), m.text()));
page.on("pageerror", (e) => console.log("PAGEERROR", String(e)));
page.on("response", (r) => {
  if (r.url().includes("/api/")) console.log("RESP", r.status(), r.url());
});

await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
const hasLogin = await page.getByLabel("board password").count();
console.log("login form present:", hasLogin);
if (hasLogin) {
  await page.getByLabel("board password").fill("board-test-pw");
  await page.getByRole("button", { name: "Sign in" }).click();
}
await page.waitForTimeout(2500);
console.log("body text head:", (await page.locator("body").innerText()).slice(0, 200));
console.log("columns:", await page.locator('[aria-label^="column "]').count());
await browser.close();
