# Assets — Screenshots & Demo Media

Add marketing visuals here for the root `README.md` and your portfolio.

## Screenshots to capture

| File | What to capture |
|------|-----------------|
| `dashboard.png` | Full dashboard with a **completed AAPL analysis** — header, stock card, agent workflow (complete), report visible |
| `agent_workflow.png` | **AgentWorkflow** mid-analysis with at least one agent in “running” state |
| `report.png` | **ReportDisplay** fully expanded — executive summary, sections, confidence meter, recommendation |
| `watchlist.png` | **WatchlistSidebar** with 3+ tickers and the current ticker highlighted |
| `mobile.png` | Responsive layout on a narrow viewport (~390px width) |

### Tips

- Use the dark theme as shipped (no browser extensions that alter colors)
- Hide personal API keys and email from any visible env/debug panels
- Prefer 1440×900 or 1280×800 for desktop shots; export PNG

## Demo GIF (optional)

1. Record screen with **QuickTime** (macOS) or **Loom**
2. Show: search `AAPL` → loading pipeline → completed report (30–45 seconds)
3. Convert to GIF:
   - **gifski**: `gifski --fps 10 --width 960 recording.mov -o demo.gif`
   - Or export from Loom directly
4. Save as `assets/demo.gif` and add to README: `![Demo](./assets/demo.gif)`

## Placeholder

Until screenshots exist, README references `./assets/dashboard.png` — add your files before publishing to GitHub.
