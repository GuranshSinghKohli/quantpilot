"use client";

import Link from "next/link";
import { ColumnHeader, NoSignal } from "@/components/hud/primitives";
import { formatWhen } from "@/lib/investigationView";
import type { ActivityItem } from "@/types";

export default function ActivityFeed({ items }: { items: ActivityItem[] }) {
  return (
    <section aria-label="Autonomous activity">
      <ColumnHeader brief="Derived from stored investigations and alerts. Steps without a stored timestamp are not invented.">
        System activity
      </ColumnHeader>
      {items.length === 0 ? (
        <NoSignal
          title="No autonomous activity yet"
          detail="When QuantPilot opens, collects, verifies, or alerts, those stored events appear here."
        />
      ) : (
        <ol className="divide-y divide-hud">
          {items.map((item, i) => (
            <li key={`${item.at}-${item.kind}-${i}`} className="py-3">
              <p className="osd-label">{formatWhen(item.at)}</p>
              {item.investigation_id ? (
                <Link
                  href={`/investigations/${item.investigation_id}`}
                  className="mt-1 block text-sm text-osd hover:text-caution"
                >
                  {item.title}
                </Link>
              ) : (
                <p className="mt-1 text-sm text-osd">{item.title}</p>
              )}
              {item.detail && (
                <p className="mt-1 text-sm text-readout">{item.detail}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
