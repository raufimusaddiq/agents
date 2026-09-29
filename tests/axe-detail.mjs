import { chromium } from "playwright-core";
import { AxeBuilder } from "@axe-core/playwright";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
const b=await chromium.launch({headless:true});
const ctx=await b.newContext({viewport:{width:1280,height:800}});
const p=await ctx.newPage();
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1200);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
await p.getByTestId("theme-toggle").click(); await p.waitForTimeout(500);
const r=await new AxeBuilder({page:p}).withRules(["color-contrast"]).analyze();
for(const v of r.violations) for(const n of v.nodes) console.log(n.target.join(" "),"|",(n.any?.[0]?.message||"").slice(0,150));
await b.close();
