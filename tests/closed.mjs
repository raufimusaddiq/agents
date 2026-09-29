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
const strip = p.locator('[aria-label="closed agents"]');
const has = await strip.count();
if (!has) { console.log("  SKIP no closed agents"); await b.close(); console.log(`\nCLOSED: ${pass} passed, ${fail} failed`); process.exit(0); }
const box = await strip.boundingBox();
chk("strip visible without scrolling", box.y + box.height <= 900, String(Math.round(box.y+box.height)));
const before = await p.getByTestId("rehire").count();
chk("collapsed by default", before === 0, String(before));
await p.locator('.board-closed-head').click(); await p.waitForTimeout(400);
chk("expands to rehire buttons", (await p.getByTestId("rehire").count()) >= 1);
await p.locator('.board-closed-head').click(); await p.waitForTimeout(300);
const doc = await p.evaluate(()=>({sh:document.documentElement.scrollHeight,ch:document.documentElement.clientHeight}));
chk("page does not scroll", doc.sh <= doc.ch + 2, `${doc.sh}/${doc.ch}`);
await p.getByTestId("theme-toggle").click(); await p.waitForTimeout(400);
console.log("  (light theme ok)");
await b.close();
console.log(`\nCLOSED: ${pass} passed, ${fail} failed`);
process.exit(fail?1:0);
