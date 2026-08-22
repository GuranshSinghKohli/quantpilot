"use client";

import gsap from "gsap";
import { useEffect, useRef, type ReactNode } from "react";
import { usePrefersReducedMotion } from "@/components/motion/usePrefersReducedMotion";

export default function PageEnter({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const node = root.current;
    if (!node || reduced) return;
    const items = node.querySelectorAll("[data-enter]");
    if (items.length === 0) return;
    gsap.fromTo(
      items,
      { opacity: 0, y: 18 },
      {
        opacity: 1,
        y: 0,
        duration: 0.7,
        stagger: 0.1,
        ease: "expo.out",
        overwrite: true,
      }
    );
  }, [reduced]);

  return <div ref={root}>{children}</div>;
}
