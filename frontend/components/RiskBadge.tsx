import type { RiskLevel } from "@/types";

interface RiskBadgeProps {
  level: RiskLevel | string | null | undefined;
  size?: "sm" | "md";
}

const CONFIG: Record<
  string,
  { label: string; bg: string; text: string; icon: string }
> = {
  LOW: {
    label: "LOW RISK",
    bg: "bg-emerald-500/15 border-emerald-500/40",
    text: "text-emerald-400",
    icon: "🛡️",
  },
  MEDIUM: {
    label: "MEDIUM RISK",
    bg: "bg-amber-500/15 border-amber-500/40",
    text: "text-amber-400",
    icon: "⚠️",
  },
  HIGH: {
    label: "HIGH RISK",
    bg: "bg-red-500/15 border-red-500/40",
    text: "text-red-400",
    icon: "🔴",
  },
};

export default function RiskBadge({ level, size = "md" }: RiskBadgeProps) {
  const key = (level ?? "MEDIUM").toString().toUpperCase();
  const config = CONFIG[key] ?? CONFIG.MEDIUM;
  const sizeClass = size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-semibold ${config.bg} ${config.text} ${sizeClass}`}
    >
      <span>{config.icon}</span>
      {config.label}
    </span>
  );
}
