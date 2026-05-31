"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0a0f",
        color: "#e2e8f0",
        padding: "2rem",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <h2 style={{ fontSize: "1.25rem", marginBottom: "0.5rem" }}>
        Something went wrong
      </h2>
      <p style={{ color: "#94a3b8", marginBottom: "1.5rem", textAlign: "center" }}>
        {error.message || "An unexpected error occurred."}
      </p>
      <button
        type="button"
        onClick={() => reset()}
        style={{
          padding: "0.5rem 1rem",
          borderRadius: "0.5rem",
          border: "1px solid #3b82f6",
          background: "rgba(59, 130, 246, 0.1)",
          color: "#60a5fa",
          cursor: "pointer",
        }}
      >
        Try again
      </button>
    </div>
  );
}
