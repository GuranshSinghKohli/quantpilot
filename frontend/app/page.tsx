import StockSearch from "@/components/StockSearch";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col px-6 py-16">
      <header className="mb-10">
        <h1 className="text-3xl font-semibold tracking-tight">QuantPilot AI</h1>
        <p className="mt-2 text-slate-400">
          Phase 1 — Stock data & SEC filings
        </p>
      </header>
      <StockSearch />
    </main>
  );
}
