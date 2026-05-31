"use client";

import { useEffect, useState } from "react";

const MESSAGES = [
  "Fetching market data...",
  "Analyzing news sentiment...",
  "Reviewing SEC filings...",
  "Assessing risk factors...",
  "Generating final report...",
];

export default function LoadingPipeline() {
  const [messageIndex, setMessageIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setMessageIndex((i) => (i + 1) % MESSAGES.length);
    }, 3500);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="card-surface space-y-4 p-6">
      <div className="flex items-center gap-3">
        <div className="h-3 w-3 animate-pulse rounded-full bg-accent" />
        <p className="text-sm font-medium text-accent">
          {MESSAGES[messageIndex]}
        </p>
      </div>
      <p className="text-xs text-slate-500">
        This usually takes 30–90 seconds — agents are fetching live data and writing your report
      </p>

      <div className="space-y-3">
        <div className="skeleton-shimmer h-8 rounded-lg" />
        <div className="skeleton-shimmer h-24 rounded-lg" />
        <div className="skeleton-shimmer h-16 rounded-lg" />
        <div className="skeleton-shimmer h-32 rounded-lg" />
        <div className="skeleton-shimmer h-20 rounded-lg" />
      </div>
    </div>
  );
}
