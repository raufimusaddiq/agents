import { chromium } from "playwright-core";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
let pass=0,fail=0; const chk=(n,c,d="")=>{c?(pass++,console.log("  PASS",n)):(fail++,console.log("  FAIL",n,d));};
const b=await chromium.launch({headless:true});
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage();
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1200);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
// open an agent with chat + screen tail
await p.locator('[aria-label^="agent "]').first().click();
await p.locator('[aria-label^="agent panel"]').waitFor({timeout:8000});
await p.waitForTimeout(2500);
const scroller = p.locator('[data-testid="chat-scroll"]');
chk("chat scroller present", (await scroller.count())>0);
if (await scroller.count()) {
  const atBottom = await scroller.evaluate((el)=> el.scrollHeight - el.scrollTop - el.clientHeight < 30);
  chk("chat pinned to bottom", atBottom);
}
const tail = p.locator('.screen-tail');
chk("screen tail present", (await tail.count())>0, String(await tail.count()));
if (await tail.count()) {
  const body = p.locator('.screen-tail-body');
  const vis = await body.isVisible();
  chk("screen tail body visible", vis);
  const sb = await body.evaluate((el)=> el.scrollHeight - el.scrollTop - el.clientHeight < 30);
  chk("screen tail pinned to bottom", sb);
  console.log("  tail sample:", (await body.innerText()).replace(/\n/g," ").slice(0,80));
}
await p.screenshot({path:"/tmp/opencode/agent-scroll.png"});
await b.close();
console.log(`\nCHAT-SCROLL: ${pass} passed, ${fail} failed`);
process.exit(fail?1:0);
