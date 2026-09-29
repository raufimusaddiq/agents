import { chromium } from "playwright-core";
import { homedir } from "node:os"; import { join } from "node:path";
process.env.LD_LIBRARY_PATH = `${join(homedir(),".local","lib","agent-board")}:${process.env.LD_LIBRARY_PATH||""}`;
let pass=0,fail=0;
const chk=(n,c,d="")=>{c?(pass++,console.log("  PASS",n)):(fail++,console.log("  FAIL",n,d))};
const b=await chromium.launch({headless:true});
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage();
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1200);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
await p.locator('[aria-label="agent boardprobe"]').click();
await p.locator('[aria-label^="agent panel"]').waitFor({timeout:8000});
const box = p.getByLabel("message agent");
async function clearAll(){
  await box.fill("");           // mirrors full clear with backspaces
  await p.waitForTimeout(1200);
}
await clearAll();
await box.fill("/");
await p.waitForTimeout(2500);
chk("slash menu appears", (await p.locator(".menu-row").count())>0, String(await p.locator(".menu-row").count()));
const firstLabel = (await p.locator(".menu-row").first().innerText()).replace(/\n/g," ");
console.log("  /  first:", firstLabel.slice(0,50));
await p.locator(".menu-row").first().click();
await p.waitForTimeout(800);
chk("pick inserts label", (await box.inputValue()).startsWith("/"), await box.inputValue());
await clearAll();
await box.fill("@");
await p.waitForTimeout(2500);
chk("at menu appears", (await p.locator(".menu-row").count())>0, String(await p.locator(".menu-row").count()));
console.log("  @  first:", ((await p.locator(".menu-row").first().innerText()).replace(/\n/g," ")).slice(0,50));
await clearAll();
await b.close();
console.log(`\nCOMPOSER: ${pass} passed, ${fail} failed`);
process.exit(fail?1:0);
