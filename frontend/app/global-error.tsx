"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0a0f",
          color: "#e0e0e0",
          fontFamily: "JetBrains Mono, ui-monospace, monospace",
        }}
      >
        <h2
          style={{
            fontSize: "2rem",
            fontWeight: 900,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            textShadow: "-2px 0 #ff00ff, 2px 0 #00d4ff",
          }}
        >
          Signal lost
        </h2>
        <p style={{ color: "#6b7280", marginTop: "0.75rem" }}>{error.message}</p>
        <button
          type="button"
          onClick={() => reset()}
          style={{
            marginTop: "1.5rem",
            padding: "0.6rem 1.1rem",
            border: 0,
            background: "#00ff88",
            color: "#0a0a0f",
            fontWeight: 700,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            cursor: "pointer",
          }}
        >
          Retry uplink
        </button>
      </body>
    </html>
  );
}
