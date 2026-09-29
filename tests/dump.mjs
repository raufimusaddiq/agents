import { chromium } from "playwright-core";
const b=await chromium.launch({headless:true});
const p=await b.newPage({viewport:{width:1280,height:800}});
await p.goto("http://127.0.0.1:8792",{waitUntil:"domcontentloaded"});
await p.waitForTimeout(1000);
if(await p.getByLabel("board password").count()){await p.getByLabel("board password").fill("board-test-pw");await p.getByRole("button",{name:"Sign in"}).click();}
await p.locator('[aria-label^="column "]').first().waitFor({timeout:15000});
for(const col of await p.locator('[aria-label^="column "]').all()){
  const name=(await col.getAttribute("aria-label")).replace("column ","");
  const cards=await col.locator('[data-testid="card"]').count();
  console.log(`COL ${name}: ${cards} cards`);
  if(cards) console.log("   ", (await col.locator('[data-testid="card"]').first().innerText()).replace(/\n/g," | ").slice(0,140));
}
await p.getByTestId("card").first().click().catch(()=>{});
await p.waitForTimeout(1500);
console.log("PANEL:", (await p.locator('[aria-label^="agent panel"]').innerText().catch(()=>"none")).slice(0,120).replace(/\n/g," | "));
await b.close();
