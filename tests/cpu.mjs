import { chromium } from "playwright-core";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
const b=await chromium.launch({headless:true});
const ctx=await b.newContext({viewport:{width:1280,height:800}});
const p=await ctx.newPage();
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1200);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
// measure task CPU of the renderer via CDP
const cdp = await ctx.newCDPSession(p);
await cdp.send("Performance.enable");
await new Promise(r=>setTimeout(r,30000));
const {metrics} = await cdp.send("Performance.getMetrics");
const m=Object.fromEntries(metrics.map(x=>[x.name,x.value]));
console.log("TaskDuration over 30s:", m.TaskDuration.toFixed(4), "s ->", (m.TaskDuration/30*100).toFixed(2), "% of one core");
await b.close();
