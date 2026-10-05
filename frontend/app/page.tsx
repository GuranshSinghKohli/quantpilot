import type { Metadata } from "next";
import ResearchDesk from "@/components/research/ResearchDesk";

export const metadata: Metadata = {
  title: "QuantPilot research",
  description: "Evidence-backed market and technology research. Findings keep their verification status and source URLs.",
};

export default function Page() {
  return <ResearchDesk />;
}
