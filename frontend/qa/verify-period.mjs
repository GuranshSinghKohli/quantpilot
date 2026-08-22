/* A week-long move must never read as today's move. */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";
const TICKER = process.env.TICKER ?? "IBM";

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});
await ctx.addInitScript(
  (id) => localStorage.setItem("quantpilot_guest_id", id),
  "local-smoke-test"
);
const page = await ctx.newPage();
await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
await page.waitForTimeout(2500);

const band = () =>
  page
    .getByRole("region", { name: "Move under investigation" })
    .innerText()
    .then((t) => t.replace(/\n+/g, " | "));

async function run(windowValue, name) {
  await page.locator(`label[for="lookback-${windowValue}"]`).click();
  await page.waitForTimeout(600);
  const button = page.getByRole("button", { name: /^Investigate/ });
  console.log(`button before ${windowValue}:`, await button.innerText());
  await page.getByLabel("Ticker to frame").fill(TICKER);
  await button.click();
  await page.waitForTimeout(80000);
  await page.evaluate(() => window.scrollTo(0, 0));
  console.log(`${windowValue}:`, (await band()).slice(0, 180));
  await page.screenshot({ path: `${OUT}${name}.png` });
}

await run("1d", "period-1d");
await run("1w", "period-1w");

await browser.close();
