"use client";

import { useEffect } from "react";

/** Tiny replacement for react-haiku's useKeyPress. */
export function useKeyPress(keys: string[], handler: () => void) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const wantMeta = keys.includes("Meta");
      const wantCtrl = keys.includes("Control");
      const letter = keys.find((k) => k.length === 1)?.toLowerCase();
      if (!letter) return;
      if (event.key.toLowerCase() !== letter) return;
      if (wantMeta && event.metaKey) handler();
      else if (wantCtrl && event.ctrlKey) handler();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keys, handler]);
}
