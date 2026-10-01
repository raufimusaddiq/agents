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
await p.getByTestId("view-toggle").click(); await p.waitForTimeout(800);
console.log("RULER:", (await p.locator('.floor-ruler').innerText()).replace(/\n/g," | "));
const lanes = await p.locator('[data-testid="lane-ticket"]').all();
for (const l of lanes.slice(0,5)) {
  const box = await l.boundingBox();
  console.log("LANE@x="+Math.round(box.x)+":", (await l.innerText()).replace(/\n/g," | ").slice(0,80));
}
console.log("robots on floor:", await p.locator('.lane-agent .robot').count());
await p.screenshot({path:"/tmp/opencode/floor2d.png",fullPage:true});
await b.close();
