import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "QuantPilot — AI Quant Research Copilot",
  description:
    "Turn any stock ticker into a research-grade equity report. Multi-agent AI analyzes market data, news, and SEC filings.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className={`${inter.className} min-h-screen font-sans`}>
        {children}
      </body>
    </html>
  );
}
