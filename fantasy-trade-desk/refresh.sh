#!/usr/bin/env bash
# Weekly refresh: re-pull ESPN league + nflverse data, rebuild projections, copy into the site folder.
set -euo pipefail
ROOT="${1:?usage: refresh.sh <data-root>}"; HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/espn" "$ROOT/nfl"
U="https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/1714013067"
curl -fsS --retry 3 -o "$ROOT/espn/league.json" "$U?view=mSettings&view=mTeam&view=mRoster&view=mMatchup&view=mStandings&view=mNav"
WK=$(python3 -I -c "import json,sys;print(json.load(open(sys.argv[1]))['scoringPeriodId'])" "$ROOT/espn/league.json")
F='{"players":{"filterStatus":{"value":["FREEAGENT","WAIVERS"]},"filterSlotIds":{"value":[0,2,4,6,16,17]},"limit":250,"sortPercOwned":{"sortPriority":1,"sortAsc":false}}}'
curl -fsS --retry 3 -H "X-Fantasy-Filter: $F" -o "$ROOT/espn/fa.json" "$U?view=kona_player_info&scoringPeriodId=$WK"
B=https://github.com/nflverse/nflverse-data/releases/download
for p in stats_player/stats_player_week_2026.csv stats_player/stats_player_week_2025.csv stats_player/stats_player_week_2024.csv \
         stats_player/stats_player_week_2023.csv stats_team/stats_team_week_2026.csv schedules/games.csv players/players.csv \
         injuries/injuries_2026.csv snap_counts/snap_counts_2026.csv pbp/play_by_play_2026.csv.gz pbp/play_by_play_2025.csv.gz; do
  curl -fsSL --retry 3 -o "$ROOT/nfl/$(basename $p)" "$B/$p"
done
echo "ESPN scoring period: $WK"
CUR_WEEK=$WK python3 -I "$HERE/build_data.py" "$ROOT" "$HERE/site/data.json"
