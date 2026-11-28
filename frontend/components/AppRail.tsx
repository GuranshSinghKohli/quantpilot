"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLenis } from "lenis/react";
import { cn } from "@/lib/utils";
import type { ToolsTab } from "@/components/ToolsDock";

const DEPARTMENTS = [
  { id: "investigate", label: "Investigate", href: "/" },
  { id: "dashboard", label: "Dashboard", href: "/dashboard" },
  { id: "history", label: "Reports", tab: "history" as ToolsTab },
] as const;

export default function AppRail({
  onTool,
}: {
  onTool?: (tab: ToolsTab) => void;
}) {
  const pathname = usePathname();
  const lenis = useLenis();
  const onDesk = pathname === "/";

  function goTool(tab: ToolsTab) {
    onTool?.(tab);
    if (lenis) lenis.scrollTo("#tools-dock", { offset: -24 });
    else document.getElementById("tools-dock")?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <nav
      aria-label="Departments"
      className="z-30 border-b border-hud bg-letterbox"
    >
      <ul className="flex items-stretch overflow-x-auto px-2 sm:px-3">
        {DEPARTMENTS.map((item, i) => {
          const active =
            item.id === "investigate"
              ? onDesk
              : item.id === "dashboard"
                ? pathname === "/dashboard" ||
                  pathname.startsWith("/investigations/") ||
                  pathname.startsWith("/companies/")
                : false;
          const className = cn(
            "whitespace-nowrap px-3 py-2.5 font-mono text-[0.6875rem] uppercase tracking-osd",
            "hover:bg-caution/10 hover:text-caution hover:shadow-neon",
            active ? "text-caution" : "text-readout",
            i > 0 && "border-l border-hud"
          );

          if (item.id === "dashboard") {
            return (
              <li key={item.id} className="flex">
                <Link href="/dashboard" className={className}>
                  {item.label}
                </Link>
              </li>
            );
          }

          if (item.id === "investigate") {
            return (
              <li key={item.id} className="flex">
                {onDesk ? (
                  <button
                    type="button"
                    className={className}
                    onClick={() => {
                      if (lenis) lenis.scrollTo("#move-under-investigation", { offset: -24 });
                      else
                        document
                          .getElementById("move-under-investigation")
                          ?.scrollIntoView({ behavior: "smooth" });
                    }}
                  >
                    {item.label}
                  </button>
                ) : (
                  <Link href="/" className={className}>
                    {item.label}
                  </Link>
                )}
              </li>
            );
          }

          const tab = "tab" in item ? item.tab : undefined;
          return (
            <li key={item.id} className="flex">
              {onDesk && tab ? (
                <button
                  type="button"
                  className={className}
                  onClick={() => goTool(tab)}
                >
                  {item.label}
                </button>
              ) : (
                <Link
                  href={tab ? `/?tool=${tab}` : "/"}
                  className={className}
                >
                  {item.label}
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
