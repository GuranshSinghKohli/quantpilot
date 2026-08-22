import { AlertTriangle, OctagonAlert, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RiskLevel } from "@/types";

interface RiskBadgeProps {
  level: RiskLevel | string | null | undefined;
  size?: "sm" | "md";
}

const CONFIG: Record<
  string,
  { label: string; icon: LucideIcon; className: string }
> = {
  LOW: {
    label: "Low risk",
    icon: ShieldCheck,
    className: "bg-up/15 text-up",
  },
  MEDIUM: {
    label: "Medium risk",
    icon: AlertTriangle,
    className: "bg-caution/15 text-caution",
  },
  HIGH: {
    label: "High risk",
    icon: OctagonAlert,
    className: "bg-down/15 text-down",
  },
};

export default function RiskBadge({ level, size = "md" }: RiskBadgeProps) {
  const key = (level ?? "MEDIUM").toString().toUpperCase();
  const config = CONFIG[key] ?? CONFIG.MEDIUM;
  const Icon = config.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 border border-hud font-semibold",
        size === "sm"
          ? "px-2 py-0.5 text-[0.625rem]"
          : "px-2.5 py-1 text-xs",
        config.className
      )}
    >
      <Icon className="h-3 w-3 shrink-0" strokeWidth={1.5} aria-hidden />
      {config.label}
    </span>
  );
}
