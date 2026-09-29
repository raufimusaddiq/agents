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
await p.waitForTimeout(800);
const docScroll = await p.evaluate(()=>({sh:document.documentElement.scrollHeight, ch:document.documentElement.clientHeight}));
console.log("document scrollHeight", docScroll.sh, "clientHeight", docScroll.ch, "page scrolls:", docScroll.sh>docScroll.ch+2);
const col = p.locator('[aria-label^="column "]').first();
const box = await col.boundingBox();
console.log("first column height", Math.round(box.height), "viewport 900");
const scroller = col.locator(".board-col-scroll");
console.log("has inner scroller:", await scroller.count());
// no New worktree button
console.log("new worktree button:", await p.getByTestId("worktree-open").count());
// footer visible without scrolling
const closed = await p.locator('.board-closed').count();
console.log("closed footer present:", closed);
await p.screenshot({path:"/tmp/opencode/scroll.png"});
await b.close();
