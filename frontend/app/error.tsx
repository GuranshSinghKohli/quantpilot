"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-letterbox px-8 py-16 text-osd">
      <h2 className="cyber-glitch font-display text-4xl font-black uppercase tracking-widest">
        Signal lost
      </h2>
      <p className="mt-3 max-w-md text-center font-mono text-sm tracking-wide text-readout">
        {error.message || "An unexpected error occurred."}
      </p>
      <button
        type="button"
        onClick={() => reset()}
        className="cyber-chamfer-sm mt-6 bg-caution px-4 py-2 font-mono text-sm font-semibold uppercase tracking-osd text-letterbox hover:shadow-neon-lg"
      >
        Retry uplink
      </button>
    </div>
  );
}
