import { chromium } from "playwright-core";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
const b=await chromium.launch({headless:true});
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage();
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1200);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
await p.waitForTimeout(1500);
console.log("counter:", await p.getByTestId("needs-counter").innerText());
console.log("needs cards:", await p.locator('[data-testid="ticket"][data-needs="true"]').count());
const chips = await p.locator('[aria-label^="agent "]').all();
for (const c of chips) {
  const lbl = await c.getAttribute("aria-label");
  const needs = await c.getAttribute("data-needs");
  console.log("  chip", lbl, "data-needs=", needs);
}
await b.close();
