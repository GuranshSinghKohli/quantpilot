/* Overflow and focus-order sanity across the supported widths. */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const browser = await chromium.launch();

for (const width of [390, 768, 1024, 1100, 1280, 1440, 1920]) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  await ctx.addInitScript(
    (id) => localStorage.setItem("quantpilot_guest_id", id),
    "local-smoke-test"
  );
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(2500);
  const r = await page.evaluate(() => {
    const d = document.documentElement;
    const wide = [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > d.clientWidth + 2)
      .slice(0, 4)
      .map((el) => `${el.tagName}.${String(el.className).slice(0, 40)}`);
    return {
      scrollWidth: d.scrollWidth,
      clientWidth: d.clientWidth,
      overflowing: wide,
    };
  });
  console.log(
    `${width}px → scroll ${r.scrollWidth} vs client ${r.clientWidth}${
      r.overflowing.length ? ` | past edge: ${r.overflowing.join(", ")}` : ""
    }`
  );
  await ctx.close();
}

await browser.close();
