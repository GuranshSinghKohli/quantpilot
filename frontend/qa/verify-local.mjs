/* Smoke check against the live local stack: no stubs, real backend. */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";
const GUEST = process.env.GUEST_ID ?? "local-smoke-test";

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});
/* Reuse the guest identity the smoke-test case was filed under, so the ledger
   has something real in it. */
await ctx.addInitScript(
  (id) => localStorage.setItem("quantpilot_guest_id", id),
  GUEST
);

const page = await ctx.newPage();
const problems = [];
page.on("console", (m) => {
  if (m.type() === "error") problems.push(`console: ${m.text()}`);
});
page.on("requestfailed", (r) =>
  problems.push(`request failed: ${r.url()} ${r.failure()?.errorText ?? ""}`)
);

await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
await page.waitForTimeout(2500);

const apiStatus = await page
  .getByText(/API (OK|DOWN)/i)
  .first()
  .innerText()
  .catch(() => "not found");
console.log("api readout:", apiStatus.trim());

/* Open the most recent real case from the ledger. */
const ledgerRow = page
  .getByRole("region", { name: "Past cases" })
  .getByRole("button", { name: /claims/ })
  .first();
if (await ledgerRow.count()) {
  await ledgerRow.click();
  await page.waitForTimeout(4000);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(600);
}

await page.screenshot({ path: `${OUT}local-live.png` });
await page.screenshot({ path: `${OUT}local-live-full.png`, fullPage: true });
console.log(problems.length ? problems.join("\n") : "no console/request errors");

await browser.close();
