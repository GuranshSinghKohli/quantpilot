"use client";

import gsap from "gsap";
import React, { useEffect, useRef, type ReactNode } from "react";
import { usePrefersReducedMotion } from "@/components/motion/usePrefersReducedMotion";

export type PresetType =
  | "fade"
  | "slide"
  | "scale"
  | "blur"
  | "blur-slide"
  | "zoom"
  | "flip"
  | "bounce"
  | "rotate"
  | "swing";

export type AnimatedGroupProps = {
  children: ReactNode;
  className?: string;
  variants?: unknown;
  preset?: PresetType;
  as?: React.ElementType;
  asChild?: React.ElementType;
};

export function AnimatedGroup({
  children,
  className,
  as: Tag = "div",
}: AnimatedGroupProps) {
  const root = useRef<HTMLElement | null>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const node = root.current;
    if (!node || reduced) return;
    const items = Array.from(node.children);
    gsap.fromTo(
      items,
      { opacity: 0, y: 10 },
      {
        opacity: 1,
        y: 0,
        duration: 0.45,
        stagger: 0.06,
        ease: "expo.out",
        overwrite: true,
      }
    );
  }, [children, reduced]);

  return (
    <Tag ref={root} className={className}>
      {children}
    </Tag>
  );
}
