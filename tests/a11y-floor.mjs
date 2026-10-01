import { chromium } from "playwright-core";
import { AxeBuilder } from "@axe-core/playwright";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
const b=await chromium.launch({headless:true});
let total=0;
for (const theme of ["dark","light"]) {
  for (const vp of [{width:1280,height:800,label:"desktop"},{width:375,height:720,label:"375"}]) {
    const ctx=await b.newContext({viewport:vp});
    const p=await ctx.newPage();
    await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
    await p.waitForTimeout(1200);
    if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
    await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
    await p.getByTestId("view-toggle").click(); await p.waitForTimeout(600);
    if(theme==="light"){await p.getByTestId("theme-toggle").click();await p.waitForTimeout(400);}
    const r=await new AxeBuilder({page:p}).analyze();
    total+=r.violations.length;
    console.log(`${theme}/${vp.label}: ${r.violations.length} axe`);
    for(const v of r.violations) console.log("   ",v.id,(v.nodes[0]?.target||[]).join(" "));
    await ctx.close();
  }
}
await b.close();
console.log(total===0?"FLOOR AXE CLEAN":`FLOOR AXE TOTAL ${total}`);
process.exit(total?1:0);
