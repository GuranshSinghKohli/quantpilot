"use client";

import type { ReactNode } from "react";

/** Compatibility shim — motion is retired; text just renders. */
export function TextEffect({
  children,
  as: Tag = "span",
  className,
}: {
  children: ReactNode;
  per?: string;
  preset?: string;
  as?: "span" | "p" | "h2" | "h3";
  speedReveal?: number;
  speedSegment?: number;
  className?: string;
}) {
  return <Tag className={className}>{children}</Tag>;
}
