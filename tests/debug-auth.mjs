import { chromium } from "playwright-core";

const BASE = "http://127.0.0.1:8792";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext();
const page = await ctx.newPage();

page.on("response", async (r) => {
  if (r.url().includes("/api/")) {
    console.log(r.status(), r.url());
    if (r.url().includes("login")) {
      console.log("  set-cookie:", r.headers()["set-cookie"]);
    }
  }
});
page.on("request", (r) => {
  if (r.url().includes("/api/")) {
    console.log("REQ", r.method(), r.url(), "origin:", r.headers()["origin"], "cookie:", r.headers()["cookie"] ? "yes" : "no");
  }
});

await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.getByLabel("board password").fill("board-test-pw");
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForTimeout(2000);
console.log("cookies:", (await ctx.cookies()).map(c => `${c.name}=${c.value.slice(0,8)} secure=${c.secure} sameSite=${c.sameSite}`));
await browser.close();
