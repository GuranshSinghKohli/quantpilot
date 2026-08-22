"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/* A brand mark makes a ticker recognisable before it is read, which is the
   whole point for someone who does not live in ticker symbols. The logo sits
   in a bracket tile so it reads as an instrument label, not a sticker; when no
   logo exists the tile falls back to a monogram rather than a broken image. */

const SIZES = {
  sm: { box: "h-6 w-6", pad: "p-0.5", text: "text-[0.625rem]" },
  md: { box: "h-8 w-8", pad: "p-1", text: "text-[0.625rem]" },
  lg: { box: "h-14 w-14 sm:h-20 sm:w-20", pad: "p-1.5", text: "text-lg" },
  watermark: {
    box: "h-48 w-48 sm:h-64 sm:w-64",
    pad: "p-0",
    text: "text-7xl",
  },
} as const;

export type CompanyMarkSize = keyof typeof SIZES;

function logoUrl(ticker: string): string {
  return `https://financialmodelingprep.com/image-stock/${encodeURIComponent(
    ticker.toUpperCase()
  )}.png`;
}

export default function CompanyMark({
  ticker,
  size = "md",
  className,
}: {
  ticker: string;
  size?: CompanyMarkSize;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const s = SIZES[size];
  const symbol = ticker.toUpperCase();
  const watermark = size === "watermark";

  useEffect(() => {
    setFailed(false);
  }, [symbol]);

  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden",
        watermark ? "bg-transparent" : "border border-hud bg-panel cyber-chamfer-sm",
        s.box,
        s.pad,
        className
      )}
      aria-hidden
    >
      {failed ? (
        <span
          className={cn(
            "font-extrabold uppercase text-readout",
            s.text
          )}
        >
          {symbol.slice(0, 2)}
        </span>
      ) : (
        /* A 24px brand mark gains nothing from the image pipeline. */
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={logoUrl(symbol)}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className="h-full w-full object-contain"
        />
      )}
    </span>
  );
}
