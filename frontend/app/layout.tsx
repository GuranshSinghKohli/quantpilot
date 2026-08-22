import type { Metadata } from "next";
import { Orbitron, JetBrains_Mono, Share_Tech_Mono } from "next/font/google";
import MotionProvider from "@/components/motion/MotionProvider";
import CornerMascot from "@/components/motion/CornerMascot";
import "./globals.css";

const display = Orbitron({
  subsets: ["latin"],
  weight: ["400", "600", "700", "800", "900"],
  variable: "--font-display",
  display: "swap",
});

const sans = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = Share_Tech_Mono({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-mono",
  display: "swap",
});

const DIRECTION_CONTRACT = `<!--
impeccable-direction seed:21b13b67 world:cyberpunk-glitch
THESIS: The investigation is a hacked terminal in the sprawl — a volatile feed of ranked claims, not a cream broadsheet or a clean brokerage desk.
OWN-WORLD: Void #0a0a0f, card #12121a, acid #00ff88, magenta #ff00ff, cyan #00d4ff, destructive #ff3366. Orbitron banners, JetBrains Mono body, Share Tech Mono labels. Chamfered clip-path panels, neon glow, scanlines, chromatic glitch. Vanta net clipped to the quote HUD.
STORY: The visitor reads the signed move as a compromised uplink, then the ranked claims, the receipts, and whether the Devil's Advocate killed the lead.
FIRST VIEWPORT: Terminal status, glitched QUANTPILOT, ticker prompt with > prefix, holographic quote HUD, three chamfered columns.
FORM: User-pinned Cyberpunk / Glitch design system. Seed key 21b13b67.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
-->`;

export const metadata: Metadata = {
  title: "QuantPilot — orchestrated equity agents",
  description:
    "Personal LangGraph + investigation-runner bench: 11 sequential research nodes, a planner that picks tools, then rank / verify / Devil's Advocate.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0a0a0f",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${sans.variable} ${mono.variable}`}
    >
      <body className="min-h-screen font-sans antialiased">
        <div
          aria-hidden
          style={{ display: "none" }}
          dangerouslySetInnerHTML={{ __html: DIRECTION_CONTRACT }}
        />
        <MotionProvider>
          {children}
          <CornerMascot />
        </MotionProvider>
      </body>
    </html>
  );
}
