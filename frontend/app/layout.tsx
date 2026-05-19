import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QuantPilot AI",
  description: "AI Quant Research Copilot — Phase 1",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
