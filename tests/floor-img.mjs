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
await p.getByTestId("view-toggle").click(); await p.waitForTimeout(900);
const bg = await p.locator('.map-floor').evaluate(el=>getComputedStyle(el).backgroundImage);
console.log("floor bg:", bg.slice(0,60));
const spriteOk = await p.locator('.worker-sprite').first().evaluate(el=>el.complete && el.naturalWidth>0);
console.log("robot sprite loaded:", spriteOk);
const wallOk = await p.locator('.wall-tile').first().evaluate(el=>el.complete && el.naturalWidth>0);
console.log("wall tile loaded:", wallOk);
await b.close();
