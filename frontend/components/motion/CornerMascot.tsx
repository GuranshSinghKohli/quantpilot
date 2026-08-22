"use client";

import { usePrefersReducedMotion } from "@/components/motion/usePrefersReducedMotion";

export default function CornerMascot() {
  const reduced = usePrefersReducedMotion();
  if (reduced) return null;

  return (
    <div className="pointer-events-none fixed bottom-3 right-3 z-[90] w-28 sm:w-36">
      <video
        src="/blueberry-headphone.webm"
        autoPlay
        loop
        muted
        playsInline
        aria-hidden
        className="h-auto w-full"
      />
    </div>
  );
}
