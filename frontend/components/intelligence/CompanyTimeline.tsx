"use client";

import Link from "next/link";
import { ColumnHeader, NoSignal } from "@/components/hud/primitives";
import { formatWhen } from "@/lib/investigationView";
import type { CompanyMemoryItem } from "@/types";

export default function CompanyTimeline({
  items,
}: {
  items: CompanyMemoryItem[];
}) {
  const groups = groupByMonth(items);

  return (
    <section aria-label="Company memory timeline">
      <ColumnHeader brief="Chronological memory from stored investigations. Months without stored items are omitted.">
        Company memory
      </ColumnHeader>
      {items.length === 0 ? (
        <NoSignal
          title="Not enough historical intelligence yet"
          detail="Themes, risks, and conclusions appear after completed investigations persist memory."
        />
      ) : (
        <ol className="space-y-5">
          {groups.map(([label, rows]) => (
            <li key={label}>
              <p className="osd-label">{label}</p>
              <ul className="mt-2 space-y-2 border-l border-hud pl-3">
                {rows.map((item) => (
                  <li key={item.id}>
                    <p className="osd-label">
                      {item.memory_type.replace(/_/g, " ")} · {item.status}
                    </p>
                    <p className="text-sm text-osd">{item.statement}</p>
                    <p className="osd-label mt-1">
                      {formatWhen(item.observed_at || item.last_seen_at)}
                    </p>
                    {item.investigation_id && (
                      <Link
                        href={`/investigations/${item.investigation_id}`}
                        className="font-mono text-sm uppercase tracking-osd text-caution hover:underline"
                      >
                        Open investigation
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function groupByMonth(items: CompanyMemoryItem[]): Array<[string, CompanyMemoryItem[]]> {
  const map = new Map<string, CompanyMemoryItem[]>();
  for (const item of items) {
    const raw = item.observed_at || item.last_seen_at;
    let label = "Undated";
    if (raw) {
      const d = new Date(raw);
      if (!Number.isNaN(d.getTime())) {
        label = d.toLocaleString(undefined, { month: "short", year: "numeric" });
      }
    }
    const list = map.get(label) || [];
    list.push(item);
    map.set(label, list);
  }
  return Array.from(map.entries());
}
