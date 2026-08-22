"use client";

import { ChevronRight } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Bracketed({
  children,
  className,
  active = false,
  quiet = false,
  large = false,
  snap = false,
  as: Tag = "div",
  ...rest
}: {
  children: ReactNode;
  className?: string;
  active?: boolean;
  quiet?: boolean;
  large?: boolean;
  snap?: boolean;
  as?: "div" | "section" | "article" | "li" | "aside" | "header" | "nav";
} & Record<string, unknown>) {
  return (
    <Tag
      className={cn(
        "bk",
        large && "bk-lg",
        quiet && "bk-quiet",
        active && "bk-active",
        snap && "bk-snap",
        className
      )}
      data-active={active ? "true" : undefined}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function SafeAreaBrackets() {
  return null;
}

export function ZebraRule({ className }: { className?: string }) {
  return (
    <div className={cn("h-px w-full bg-down", className)} aria-hidden />
  );
}

export function ZebraEdge({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-x-0 top-0 h-1 bg-down",
        className
      )}
      aria-hidden
    />
  );
}

export function Readout({
  label,
  tone = "neutral",
  live = false,
  className,
}: {
  label: string;
  tone?: "neutral" | "caution" | "quiet";
  live?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-0.5",
        "osd-readout whitespace-nowrap border border-hud",
        tone === "caution" && "border-caution text-caution shadow-neon",
        tone === "quiet" && "border-transparent text-readout",
        className
      )}
    >
      {live && (
        <span
          className="h-1.5 w-1.5 shrink-0 bg-caution animate-blink"
          aria-hidden
        />
      )}
      {label}
    </span>
  );
}

export function ColumnHeader({
  children,
  aside,
  brief,
}: {
  children: ReactNode;
  aside?: ReactNode;
  brief?: string;
}) {
  return (
    <div className="mb-3 border-b border-hud pb-2">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="osd-header">{children}</h2>
        {aside && <span className="osd-label shrink-0">{aside}</span>}
      </div>
      {brief && (
        <p className="mt-1.5 text-sm leading-relaxed tracking-wide text-readout">
          {brief}
        </p>
      )}
    </div>
  );
}

export function WeightBar({
  value,
  lead = false,
  label,
}: {
  value: number;
  lead?: boolean;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div className="flex items-center gap-3">
      <div
        className="h-1.5 flex-1 overflow-hidden bg-hud-deep"
        role="img"
        aria-label={`Evidence weight ${(pct * 100).toFixed(0)} percent`}
      >
        <div
          className={cn(
            "h-full origin-left animate-bar-fill",
            lead ? "bg-caution shadow-neon" : "bg-cyan"
          )}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      <span
        data-figures
        className={cn(
          "osd-readout w-10 shrink-0 text-right",
          lead ? "text-caution" : "text-readout"
        )}
      >
        {label ?? `${Math.round(pct * 100)}%`}
      </span>
    </div>
  );
}

export function Stamp({
  children,
  tone = "caution",
  title,
}: {
  children: ReactNode;
  tone?: "caution" | "osd";
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "shrink-0 border px-1.5 py-0.5 font-mono text-[0.625rem] uppercase tracking-osd",
        tone === "caution"
          ? "border-down text-down shadow-magenta"
          : "border-hud text-readout"
      )}
    >
      {children}
    </span>
  );
}

type HudButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost";
  loading?: boolean;
  children: ReactNode;
};

export function HudButton({
  variant = "secondary",
  loading = false,
  children,
  className,
  disabled,
  ...rest
}: HudButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "relative inline-flex min-h-11 items-center justify-center gap-2 px-4 py-2.5",
        "font-mono text-sm font-semibold uppercase tracking-osd",
        "transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-45",
        "cyber-chamfer-sm",
        variant === "primary" &&
          "cyber-glitch bg-caution text-letterbox hover:brightness-110 hover:shadow-neon-lg",
        variant === "secondary" &&
          "border-2 border-caution bg-transparent text-caution hover:bg-caution hover:text-letterbox hover:shadow-neon",
        variant === "ghost" &&
          "text-readout hover:bg-caution/10 hover:text-caution",
        className
      )}
    >
      {loading && (
        <span
          className="h-3 w-3 shrink-0 animate-spin border border-current border-t-transparent"
          aria-hidden
        />
      )}
      {children}
    </button>
  );
}

export function WindowBar<T extends string>({
  options,
  value,
  onChange,
  disabled = false,
  name,
}: {
  options: { value: T; label: string; hint?: string }[];
  value: T;
  onChange: (next: T) => void;
  disabled?: boolean;
  name: string;
}) {
  const index = Math.max(
    0,
    options.findIndex((opt) => opt.value === value)
  );
  const active = options[index];

  return (
    <div className={cn("w-full min-w-[16rem]", disabled && "opacity-45")}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="osd-label">Look back</span>
        <span className="font-mono text-sm uppercase tracking-wide text-caution">
          {active?.hint ?? active?.label}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={options.length - 1}
        step={1}
        value={index}
        disabled={disabled}
        aria-label="Look-back window"
        aria-valuetext={active?.hint ?? active?.label}
        onChange={(event) => {
          const next = options[Number(event.target.value)];
          if (next) onChange(next.value);
        }}
        className="window-slider"
      />
      <div
        role="radiogroup"
        aria-label="Look-back window"
        className="mt-2 flex justify-between"
      >
        {options.map((opt) => {
          const selected = opt.value === value;
          const id = `${name}-${opt.value}`;
          return (
            <label key={opt.value} htmlFor={id} className="cursor-pointer">
              <input
                id={id}
                type="radio"
                name={name}
                value={opt.value}
                checked={selected}
                disabled={disabled}
                onChange={() => onChange(opt.value)}
                className="sr-only"
              />
              <span
                className={cn(
                  "font-mono text-xs uppercase tracking-osd",
                  selected ? "text-caution" : "text-readout"
                )}
              >
                {opt.label}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

export function TapeCounter({
  label = "Case",
  value,
}: {
  label?: string;
  value: string;
}) {
  return (
    <span className="inline-flex items-center gap-2 border border-hud px-3 py-1">
      <span className="osd-label">{label}</span>
      <span data-figures className="osd-readout text-caution">
        {value}
      </span>
    </span>
  );
}

export function SourceTile({ source }: { source: string }) {
  const tag = source.replace(/[^A-Za-z0-9]/g, "").slice(0, 4).toUpperCase();
  return (
    <span
      className="flex h-7 w-9 shrink-0 items-center justify-center border border-hud font-mono text-[0.5625rem] tracking-osd text-cyan"
      title={source}
    >
      {tag || "SRC"}
    </span>
  );
}

export function RowChevron({ className }: { className?: string }) {
  return (
    <ChevronRight
      className={cn("h-4 w-4 shrink-0 text-readout", className)}
      strokeWidth={1.5}
      aria-hidden
    />
  );
}

export function NoSignal({
  title,
  detail,
  action,
  compact = false,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start text-left",
        compact ? "py-3" : "py-6"
      )}
    >
      <p className="osd-header">{title}</p>
      {detail && (
        <p className="mt-2 max-w-sm text-sm leading-relaxed tracking-wide text-readout">
          {detail}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Caution({
  title,
  children,
  role = "alert",
}: {
  title: string;
  children?: ReactNode;
  role?: "alert" | "status";
}) {
  return (
    <div
      role={role}
      className="border border-down bg-caution-50 px-4 py-3 cyber-chamfer-sm"
    >
      <p className="font-mono text-sm font-semibold uppercase tracking-wide text-down">
        {title}
      </p>
      {children && (
        <div className="mt-1.5 text-sm leading-relaxed text-osd">{children}</div>
      )}
    </div>
  );
}
