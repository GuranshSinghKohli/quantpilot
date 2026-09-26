import { Bracketed, ColumnHeader } from "@/components/hud/primitives";
import {
  CURRENT_STACK,
  PROJECT_LINE,
  TARGET_STACK,
  WORKFLOW,
} from "@/lib/stack";

export default function ProjectStack() {
  return (
    <Bracketed as="section" aria-label="Project stack" className="p-3 sm:p-4">
      <ColumnHeader brief="in repo">Stack</ColumnHeader>
      <p className="font-mono text-xs uppercase tracking-osd text-caution">
        {PROJECT_LINE}
      </p>
      <p className="mt-2 font-mono text-sm leading-relaxed text-osd">
        {WORKFLOW}
      </p>
      <p className="mt-3 text-sm leading-relaxed text-readout">
        Built so a reviewer can see the pipeline, the evidence, and the
        technologies — not a black-box summary.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <StackList title="Shipped in this repo" rows={CURRENT_STACK} />
        <StackList title="Spec next — not shipped" rows={TARGET_STACK} quiet />
      </div>
    </Bracketed>
  );
}

function StackList({
  title,
  rows,
  quiet = false,
}: {
  title: string;
  rows: { layer: string; tech: string }[];
  quiet?: boolean;
}) {
  return (
    <div>
      <p className="osd-label">{title}</p>
      <dl className="mt-2 space-y-1.5">
        {rows.map((row) => (
          <div key={row.layer} className="flex gap-3 text-sm">
            <dt
              className={
                quiet
                  ? "w-16 shrink-0 font-mono text-readout"
                  : "w-16 shrink-0 font-mono text-caution"
              }
            >
              {row.layer}
            </dt>
            <dd className="text-osd">{row.tech}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
