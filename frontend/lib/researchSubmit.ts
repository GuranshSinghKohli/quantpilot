export function submitBody(query: string): { query: string } {
  const text = query.trim();
  if (!text) throw new Error("query is required");
  return { query: text };
}

export async function submitResearch(
  fetchImpl: typeof fetch,
  url: string,
  query: string,
  signal?: AbortSignal
): Promise<{ research_id: string; status: string; status_url?: string }> {
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(submitBody(query)),
    signal,
  });
  if (!response.ok) {
    throw new Error(`research submit failed (${response.status})`);
  }
  return response.json() as Promise<{ research_id: string; status: string; status_url?: string }>;
}
