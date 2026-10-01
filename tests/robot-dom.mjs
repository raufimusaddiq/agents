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
await p.waitForTimeout(600);
const svg = await p.locator('.robot').first().evaluate(el=>el.outerHTML);
console.log(svg.slice(0,700));
const cls = await p.locator('.robot').first().getAttribute('class');
console.log("CLASS:", cls);
const anim = await p.locator('.robot').first().evaluate(el=>{
  const arm=el.querySelector('.robot-arm-l'); return arm?getComputedStyle(arm).animationName:'none';
});
console.log("arm animation:", anim);
await b.close();
