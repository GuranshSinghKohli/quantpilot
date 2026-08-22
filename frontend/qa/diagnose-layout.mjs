/* Measures the real usable width of the elements that are wrapping badly. */
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";
const WIDTHS = [1100, 1280, 1440, 1024, 820];

const browser = await chromium.launch();

for (const width of WIDTHS) {
  const ctx = await browser.newContext({
    viewport: { width, height: 900 },
    deviceScaleFactor: 2,
  });
  await ctx.addInitScript(
    (id) => localStorage.setItem("quantpilot_guest_id", id),
    "local-smoke-test"
  );
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(3000);

  const m = await page.evaluate(() => {
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.width);
    };
    const receipts = document.querySelector(
      '[aria-label="Receipts collected for this case"]'
    );
    const firstTitle = receipts?.querySelectorAll("li span.min-w-0 > span")[0];
    const titleWidth = firstTitle
      ? Math.round(firstTitle.getBoundingClientRect().width)
      : null;
    const titleLines = firstTitle
      ? Math.round(
          firstTitle.getBoundingClientRect().height /
            parseFloat(getComputedStyle(firstTitle).lineHeight)
        )
      : null;
    return {
      causes: box('[aria-label="What caused the move"]'),
      receipts: box('[aria-label="Receipts collected for this case"]'),
      position: box('[aria-label="Your position"]'),
      titleWidth,
      titleLines,
      titleText: firstTitle?.textContent?.slice(0, 46),
    };
  });

  console.log(
    `${width}px → causes ${m.causes} · receipts ${m.receipts} · position ${m.position} | receipt title ${m.titleWidth}px over ~${m.titleLines} lines ("${m.titleText}")`
  );
  await page.screenshot({ path: `${OUT}diag-${width}.png` });
  await ctx.close();
}

await browser.close();
