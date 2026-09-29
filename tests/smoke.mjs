import { chromium } from "playwright-core";

const BASE = process.env.BOARD_URL || "http://127.0.0.1:8792";
const PW = process.env.BOARD_PW || "board-test-pw";
const OUT = process.env.OUT_DIR || "/tmp/opencode";

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

// dark, desktop
{
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await login(page);
  const cols = await page.locator('[aria-label^="column "]').count();
  console.log("desktop columns:", cols);
  await page.screenshot({ path: `${OUT}/board-dark.png`, fullPage: true });
  // toggle light
  await page.getByTestId("theme-toggle").click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/board-light.png`, fullPage: true });
  await page.close();
}

// 375px mobile
{
  const page = await browser.newPage({ viewport: { width: 375, height: 720 } });
  await login(page);
  await page.screenshot({ path: `${OUT}/board-375.png`, fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth);
  console.log("375 scrollWidth:", overflow);
  await page.close();
}

await browser.close();
console.log("SMOKE OK");
