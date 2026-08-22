import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      screens: {
        desk: "880px",
      },
      colors: {
        letterbox: "#0a0a0f",
        panel: "#12121a",
        hud: {
          DEFAULT: "#2a2a3a",
          deep: "#1c1c2e",
          line: "#2a2a3a",
        },
        osd: "#e0e0e0",
        readout: "#6b7280",
        caution: {
          DEFAULT: "#00ff88",
          deep: "#00c46a",
          50: "rgba(255, 51, 102, 0.12)",
          100: "rgba(0, 255, 136, 0.12)",
          200: "#66ffb5",
          300: "#33ff9e",
          400: "#00ff88",
          500: "#00ff88",
          600: "#00c46a",
        },
        accent: "#00ff88",
        magenta: "#ff00ff",
        cyan: "#00d4ff",
        up: "#00ff88",
        down: "#ff3366",
        emerald: {
          400: "#00ff88",
          500: "#00ff88",
        },
        green: {
          400: "#00ff88",
          500: "#00ff88",
        },
        red: {
          200: "#ff99b0",
          300: "#ff6688",
          400: "#ff3366",
          500: "#ff3366",
        },
        amber: {
          400: "#ff00ff",
          500: "#ff00ff",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "Orbitron", "ui-monospace", "monospace"],
        sans: ["var(--font-sans)", "JetBrains Mono", "ui-monospace", "monospace"],
        mono: ["var(--font-mono)", "Share Tech Mono", "ui-monospace", "monospace"],
        serif: ["var(--font-display)", "Orbitron", "ui-monospace", "monospace"],
      },
      borderRadius: {
        none: "0",
        sm: "2px",
        DEFAULT: "0",
        md: "4px",
        lg: "0",
        xl: "0",
        "2xl": "0",
        "3xl": "0",
        full: "0",
      },
      boxShadow: {
        sm: "0 0 3px #00ff88, 0 0 6px #00ff8830",
        DEFAULT: "0 0 5px #00ff88, 0 0 10px #00ff8840",
        md: "0 0 5px #00ff88, 0 0 10px #00ff8840",
        lg: "0 0 10px #00ff88, 0 0 20px #00ff8860, 0 0 40px #00ff8830",
        card: "0 0 5px #00ff8840",
        neon: "0 0 5px #00ff88, 0 0 10px #00ff8840",
        "neon-lg": "0 0 10px #00ff88, 0 0 20px #00ff8860, 0 0 40px #00ff8830",
        magenta: "0 0 5px #ff00ff, 0 0 20px #ff00ff60",
        cyan: "0 0 5px #00d4ff, 0 0 20px #00d4ff60",
      },
      letterSpacing: {
        osd: "0.2em",
        readout: "0.08em",
      },
      keyframes: {
        "bar-fill": {
          from: { transform: "scaleX(0)" },
          to: { transform: "scaleX(1)" },
        },
        "tape-run": {
          from: { transform: "translateX(0)" },
          to: { transform: "translateX(-50%)" },
        },
        "rec-blink": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        blink: {
          "50%": { opacity: "0" },
        },
        glitch: {
          "0%, 100%": { transform: "translate(0)" },
          "20%": { transform: "translate(-2px, 2px)" },
          "40%": { transform: "translate(2px, -2px)" },
          "60%": { transform: "translate(-1px, -1px)" },
          "80%": { transform: "translate(1px, 1px)" },
        },
        rgbShift: {
          "0%, 100%": { textShadow: "-2px 0 #ff00ff, 2px 0 #00d4ff" },
          "50%": { textShadow: "2px 0 #ff00ff, -2px 0 #00d4ff" },
        },
        scanline: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100vh)" },
        },
      },
      animation: {
        "bar-fill": "bar-fill 0.7s cubic-bezier(0.4, 0, 0.2, 1) both",
        "tape-run": "tape-run 28s linear infinite",
        "rec-blink": "blink 1s step-end infinite",
        blink: "blink 1s step-end infinite",
        glitch: "glitch 0.35s steps(2) infinite",
        "rgb-shift": "rgbShift 4s steps(2) infinite",
        scanline: "scanline 8s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
