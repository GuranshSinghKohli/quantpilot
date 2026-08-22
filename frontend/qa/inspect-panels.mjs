/* Close reads of the panels being reworked, at the width in question. */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";
const WIDTH = Number(process.env.WIDTH ?? 1100);

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: WIDTH, height: 900 },
  deviceScaleFactor: 2,
});
await ctx.addInitScript(
  (id) => localStorage.setItem("quantpilot_guest_id", id),
  "local-smoke-test"
);
const page = await ctx.newPage();
await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
await page.waitForTimeout(3000);

const shots = [
  ["chat", /^Ask about the /],
  ["ledger", "Past cases"],
];
for (const [name, label] of shots) {
  const region = page.getByRole("region", { name: label }).first();
  if ((await region.count()) === 0) {
    console.log(`missing region: ${name}`);
    continue;
  }
  await region.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  await region.screenshot({ path: `${OUT}panel-${name}-${WIDTH}.png` });
  console.log(`captured ${name}`);
}

await page.evaluate(() => window.scrollTo(0, 0));
await page.screenshot({ path: `${OUT}page-${WIDTH}-full.png`, fullPage: true });
await browser.close();
