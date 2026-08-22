/* What a brand-new visitor sees, and what a returning one sees. */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";

const browser = await chromium.launch();

async function shot(name, { guest, width = 1440, height = 900, motion }) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    reducedMotion: motion,
  });
  if (guest) {
    await ctx.addInitScript(
      (id) => localStorage.setItem("quantpilot_guest_id", id),
      guest
    );
  }
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `${OUT}${name}.png` });
  await page.screenshot({ path: `${OUT}${name}-full.png`, fullPage: true });
  const band = await page
    .getByRole("region", { name: "Move under investigation" })
    .innerText();
  console.log(`${name}: ${band.replace(/\n+/g, " | ").slice(0, 220)}`);
  await ctx.close();
}

// A visitor with no history at all: the bundled example should stand in.
await shot("arrive-newcomer", { guest: `fresh-${Date.now()}` });
// A returning visitor: their newest real case should open.
await shot("arrive-returning", { guest: "local-smoke-test" });
await shot("arrive-newcomer-mobile", {
  guest: `fresh-m-${Date.now()}`,
  width: 390,
  height: 844,
});
// The counting figure and staggered rows must not swallow content when the
// visitor has asked the system for less movement.
await shot("arrive-reduced-motion", {
  guest: `fresh-r-${Date.now()}`,
  motion: "reduce",
});

await browser.close();
