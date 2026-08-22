"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { cn } from "@/lib/utils";
import type { VantaInstance } from "vanta";

export default function FieldBackground({
  busy = false,
  className,
}: {
  busy?: boolean;
  className?: string;
}) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = el.current;
    if (!node) return;

    let instance: VantaInstance | null = null;
    let cancelled = false;
    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;

    async function mount() {
      const mod = await import("vanta/dist/vanta.net.min");
      if (cancelled || !el.current) return;
      const create = mod.default;
      instance = create({
        el: el.current,
        THREE,
        mouseControls: false,
        touchControls: false,
        gyroControls: false,
        minHeight: 160,
        minWidth: 160,
        scale: 1,
        scaleMobile: 0.55,
        color: 0x00ff88,
        backgroundColor: 0x0a0a0f,
        points: 8,
        maxDistance: reduced ? 0 : busy ? 22 : 16,
        spacing: 20,
        showDots: false,
      });
    }

    void mount();

    const onVis = () => {
      if (!instance) return;
      const hidden = document.hidden || reduced;
      instance.setOptions({
        maxDistance: hidden ? 0 : busy ? 22 : 16,
      });
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVis);
      instance?.destroy();
    };
  }, [busy]);

  return (
    <div
      ref={el}
      className={cn(
        "pointer-events-none absolute inset-0 opacity-40",
        className
      )}
      aria-hidden
    />
  );
}
