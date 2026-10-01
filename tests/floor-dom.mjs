import { chromium } from "playwright-core";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
const b=await chromium.launch({headless:true});
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage();
p.on("pageerror", e=>console.log("PAGEERROR",String(e).slice(0,200)));
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1200);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
await p.getByTestId("view-toggle").click(); await p.waitForTimeout(800);
console.log("benches:", await p.locator('.bench').count(), "workers:", await p.locator('.worker').count(), "job cards:", await p.locator('.job-card').count());
for (const w of (await p.locator('.worker-slot').all()).slice(0,4)) {
  const box = await w.boundingBox();
  if (box) console.log(" worker@", Math.round(box.x), Math.round(box.y), (await w.innerText()).replace(/\n/g," ").slice(0,30));
}
await p.screenshot({path:"/tmp/opencode/shop.png",fullPage:true});
await b.close();
