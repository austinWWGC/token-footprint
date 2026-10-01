# Token Footprint widget

A small always-on-top Windows widget. It shows how many tokens your Claude Code and Cowork sessions have used and converts them to estimated electricity, carbon, and water.

## Install

1. Download `TokenFootprint.exe`. You don't need to install Python or anything else.
2. Double-click it. The first time, it offers to put a shortcut on your desktop.
3. **If Windows shows "Windows protected your PC":** click **More info → Run anyway**. Windows shows this warning for any app that isn't code-signed. It doesn't mean anything was detected.

Requires Windows 10 or 11. You'll only see numbers if you use Claude Code (terminal, VS Code/JetBrains, or the desktop app's Code tab) or Cowork.

## Using it

- **Drag** the widget anywhere. It remembers where you left it.
- **Click** Today / 7 days / 30 days / All to change the time period.
- **Right-click** for: refresh, always on top, **Start with Windows**, add desktop shortcut, edit conversion factors, quit.

## Privacy

The widget reads only log files already on your computer. It makes **no network connections**, sends nothing anywhere, and never reads the text of your conversations; it only reads the token counts. Its settings live in `%APPDATA%\TokenFootprint`.

**To uninstall:** turn off "Start with Windows" (right-click menu), quit, then delete the `.exe`, the desktop shortcut, and `%APPDATA%\TokenFootprint`.

## What it counts

Every reply Claude Code sends is logged, with its token counts, in `%USERPROFILE%\.claude\projects\**\*.jsonl`. That includes Code-tab sessions in the Claude desktop app and subagents. Cowork sessions from the desktop app are also counted: they're logged in the same format under `%APPDATA%\Claude\local-agent-mode-sessions`, or in the `Packages\Claude_*` folder when the app is the Store version. The widget re-reads only new lines every 30 seconds.

**Not counted:** regular claude.ai chats (web, desktop Chat tab, mobile). Their token counts are never stored on your computer.

## How the math works

```
IT energy (Wh) = model multiplier × (input × 0.0002 + cache writes × 0.0002
                                     + cache reads × 0.00002 + output × 0.001)
Facility energy = IT energy × PUE (1.2)
Carbon          = kWh × 0.37 kg CO2e/kWh
Water           = kWh × (0.5 L on-site cooling + 3.1 L off-site, from power generation)
```

Every number is in `config.json`. Edit it and the widget picks up changes on its next refresh.

## Where the defaults come from (and how shaky they are)

Anthropic does not publish energy-per-token figures, so **treat every output as an order-of-magnitude estimate, not a measurement.** The defaults are mid-range values based on public research:

| Factor | Default | Basis |
|---|---|---|
| Output tokens | 0.001 Wh/token | Epoch AI (2025) put a typical GPT-4o reply at ~0.3 Wh for ~500 output tokens (~0.0006 Wh/token). Frontier models like Opus are likely larger, so this is rounded up. |
| Input / cache write | 0.0002 Wh/token | Processing input (prefill) is far cheaper per token than generating output. Epoch AI's long-context estimates land around 0.0002–0.0004 Wh/token. |
| Cache read | 0.00002 Wh/token | Cached context skips most of the recompute. Assumed ~10% of fresh input. |
| Model multipliers | Opus 1.0, Sonnet 0.5, Haiku 0.15 | Smaller models use less compute per token. These ratios are guesses. |
| PUE | 1.2 | Typical hyperscale data-center overhead (cooling, power conversion). |
| Grid carbon | 0.37 kg CO2e/kWh | Approximate US grid average (EPA eGRID / EIA). Real values depend on where and when the data center runs. |
| Water, on-site | 0.5 L/kWh | Typical hyperscaler cooling water use (WUE). |
| Water, off-site | 3.1 L/kWh | US average water consumed to generate electricity (Li et al., *Making AI Less "Thirsty"*, 2023). Set to 0 to count cooling water only. |

For comparison, Google reported (Aug 2025) that a median Gemini text prompt uses 0.24 Wh, 0.03 g CO2e, and 0.26 mL of water. Claude Code requests are much bigger than a typical chat prompt, since each one resends tens of thousands of tokens of project context. That's why a coding session adds up quickly.

**Equivalents:** 15 Wh per smartphone charge, 400 g CO2 per mile in an average car (EPA), 0.5 L per 16.9 oz water bottle.
