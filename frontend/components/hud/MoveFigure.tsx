"use client";

import gsap from "gsap";
import { useEffect, useRef, useState } from "react";
import { usePrefersReducedMotion } from "@/components/motion/usePrefersReducedMotion";
import { cn } from "@/lib/utils";

export default function MoveFigure({
  ticker,
  movePct,
}: {
  ticker: string;
  movePct: number | null;
}) {
  const reduced = usePrefersReducedMotion();
  const target = movePct ?? 0;
  const [shown, setShown] = useState(target);
  const obj = useRef({ value: target });

  useEffect(() => {
    if (movePct == null) return;
    if (reduced) {
      setShown(movePct);
      return;
    }
    obj.current.value = 0;
    const tween = gsap.to(obj.current, {
      value: movePct,
      duration: 1.05,
      ease: "expo.out",
      onUpdate: () => setShown(obj.current.value),
    });
    return () => {
      tween.kill();
    };
  }, [movePct, reduced]);

  if (movePct == null) return <>{ticker}</>;

  const down = shown < 0;
  const sign = down ? "−" : "+";
  return (
    <span className={cn(down ? "text-down" : "text-up")}>
      {ticker} {sign}
      {Math.abs(shown).toFixed(2)}%
    </span>
  );
}
