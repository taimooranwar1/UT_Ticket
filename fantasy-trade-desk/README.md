# Hell In a Cell Trade Desk

Interactive fantasy football trade calculator for ESPN league 1714013067 (2026).
It compares any trade's effect on weekly matchups, playoff odds (top 4 of 8) and title odds.

- `build_data.py` builds `site/data.json`: player projections (baseline × matchup × game environment × history × availability), backtest, variance model.
- `site/index.html` is the page. It runs the league Monte Carlo in the browser (with and without the trade, same random draws).
- `refresh.sh <data-dir>` re-downloads ESPN league data and nflverse stats into `<data-dir>` (raw files are not committed) and rebuilds `site/data.json` for ESPN's current week.

Weekly refresh:

```bash
./fantasy-trade-desk/refresh.sh /tmp/trade-desk-data
```

The league must stay viewable to the public for the ESPN calls to work.
Data sources: ESPN fantasy API (league, rosters, standings, free agents, projections) and nflverse (stats, play-by-play, injuries, schedule, Vegas lines).
