/* Does picking a new look-back actually re-open the case? */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";
const GUEST = "local-smoke-test";

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});
await ctx.addInitScript(
  (id) => localStorage.setItem("quantpilot_guest_id", id),
  GUEST
);
const page = await ctx.newPage();
await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
await page.waitForTimeout(2000);

const ledgerRow = page
  .getByRole("region", { name: "Past cases" })
  .getByRole("button", { name: /claims/ })
  .first();
await ledgerRow.click();
await page.waitForTimeout(4000);
await page.evaluate(() => window.scrollTo(0, 0));

const headline = () =>
  page.getByRole("region", { name: "Move under investigation" }).innerText();

console.log("before:", (await headline()).replace(/\n+/g, " | ").slice(0, 160));

/* Deliberately a window the open case did not measure, so the re-run fires. */
await page.locator('label[for="lookback-1mo"]').click();
await page.waitForTimeout(2000);
console.log(
  "during:",
  (await headline()).replace(/\n+/g, " | ").slice(0, 160)
);

// The pipeline takes ~40s against live data.
await page.waitForTimeout(75000);
console.log("after: ", (await headline()).replace(/\n+/g, " | ").slice(0, 160));

await page.screenshot({ path: `${OUT}local-window-switch.png` });
await browser.close();
