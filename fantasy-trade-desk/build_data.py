"""Build projection data for the fantasy trade calculator.

Inputs : ESPN league JSON (league.json, fa.json) + nflverse CSVs.
Output : data.json consumed by the HTML page (simulation runs in-browser).

Model (per player, per week):
    proj = baseline x matchup x environment x history x availability
"""
import csv, gzip, json, math, sys, os, collections, datetime
import numpy as np

ROOT = sys.argv[1]
ESPN = os.path.join(ROOT, "espn")
NFL = os.path.join(ROOT, "nfl")
OUT = sys.argv[2]

SEASON = 2026
CUR_WEEK = int(os.environ.get("CUR_WEEK", "11"))
PROJ_WEEKS = list(range(CUR_WEEK, 18))          # 11-14 regular season, 15 semis, 16-17 final
REG_WEEKS = [w for w in range(CUR_WEEK, 15)]
MY_TEAM = 8

ESPN_TEAM = {1:"ATL",2:"BUF",3:"CHI",4:"CIN",5:"CLE",6:"DAL",7:"DEN",8:"DET",9:"GB",10:"TEN",
             11:"IND",12:"KC",13:"LV",14:"LA",15:"MIA",16:"MIN",17:"NE",18:"NO",19:"NYG",20:"NYJ",
             21:"PHI",22:"ARI",23:"PIT",24:"LAC",25:"SF",26:"SEA",27:"TB",28:"WAS",29:"CAR",30:"JAX",
             33:"BAL",34:"HOU"}
POS = {1:"QB",2:"RB",3:"WR",4:"TE",5:"K",16:"DST"}
SLOT = {0:"QB",2:"RB",4:"WR",6:"TE",23:"FLEX",16:"DST",17:"K",20:"BN",21:"IR"}
SKILL = ("QB","RB","WR","TE")

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

def clamp(x, lo, hi): return max(lo, min(hi, x))

# ---------------------------------------------------------------- load nflverse
def load_csv(name):
    path = os.path.join(NFL, name)
    op = gzip.open if name.endswith(".gz") else open
    with op(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))

games = [g for g in load_csv("games.csv") if g["season"] in ("2023","2024","2025","2026") and g["game_type"] == "REG"]
g26 = [g for g in games if g["season"] == "2026"]

def kicker_fp(r):
    made3 = f(r["fg_made_0_19"]) + f(r["fg_made_20_29"]) + f(r["fg_made_30_39"])
    return 3*made3 + 4*f(r["fg_made_40_49"]) + 5*f(r["fg_made_50_59"]) + 6*f(r["fg_made_60_"]) \
           - 1*f(r["fg_missed"]) + f(r["pat_made"])

def player_rows(season):
    rows = []
    for r in load_csv(f"stats_player_week_{season}.csv"):
        if r["season_type"] != "REG": continue
        pos = r["position"]
        if pos in ("FB",): pos = "RB"
        if pos == "K": fp = kicker_fp(r)
        elif pos in SKILL: fp = f(r["fantasy_points_ppr"])
        else: continue
        rows.append(dict(pid=r["player_id"], name=r["player_display_name"], pos=pos, season=season,
                         week=int(r["week"]), team=r["team"], opp=r["opponent_team"], fp=fp,
                         targets=f(r["targets"]), carries=f(r["carries"]), att=f(r["attempts"])))
    return rows

P = {s: player_rows(s) for s in (2023, 2024, 2025, 2026)}
p26 = P[2026]
LAST_WEEK = max(r["week"] for r in p26)
print("nflverse 2026 data through week", LAST_WEEK)

# ---------------------------------------------------------------- DST fantasy points (league scoring)
def pa_points(pa):
    if pa == 0: return 5
    if pa <= 6: return 4
    if pa <= 13: return 3
    if pa <= 17: return 1
    if pa <= 27: return 0
    if pa <= 34: return -1
    if pa <= 45: return -3
    return -5

def ya_points(ya):
    if ya < 100: return 5
    if ya < 200: return 3
    if ya < 300: return 2
    if ya < 350: return 0
    if ya < 400: return -1
    if ya < 450: return -3
    if ya < 500: return -5
    if ya < 550: return -6
    return -7

team_rows = load_csv("stats_team_week_2026.csv")
team_rows = [r for r in team_rows if r["season_type"] == "REG"]
by_team_week = {(r["team"], int(r["week"])): r for r in team_rows}
score = {}
for g in g26:
    if g["home_score"] == "": continue
    w = int(g["week"])
    score[(g["home_team"], w)] = (int(g["home_score"]), int(g["away_score"]))
    score[(g["away_team"], w)] = (int(g["away_score"]), int(g["home_score"]))

dst_rows = []
for (team, w), r in by_team_week.items():
    if (team, w) not in score: continue
    o = by_team_week.get((r["opponent_team"], w))
    if not o: continue
    pa = score[(team, w)][1]
    ya = f(o["passing_yards"]) - f(o["sack_yards_lost"]) + f(o["rushing_yards"])
    pts = (f(r["def_sacks"]) * 1 + f(r["def_interceptions"]) * 2 + f(r["fumble_recovery_opp"]) * 2
           + f(r["def_safeties"]) * 2 + (f(r["def_fg_blocks"]) + f(r["def_punt_blocks"])) * 2
           + (f(r["def_tds"]) + f(r["special_teams_tds"])) * 6 + pa_points(pa) + ya_points(ya))
    dst_rows.append(dict(pid="DST_" + team, name=team + " D/ST", pos="DST", season=2026, week=w,
                         team=team, opp=r["opponent_team"], fp=pts, targets=0, carries=0, att=0))
p26_all = p26 + dst_rows

# ---------------------------------------------------------------- usage-based expected points (xFP)
def pbp_features(season):
    feats = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in load_csv(f"play_by_play_{season}.csv.gz"):
        if r["season_type"] != "REG" or r["two_point_attempt"] == "1": continue
        pt = r["play_type"]
        if pt not in ("pass", "run"): continue
        w = int(r["week"]); yl = f(r["yardline_100"]); rz = yl <= 10 and r["yardline_100"] != ""
        if pt == "pass":
            qb = r["passer_player_id"]
            if qb:
                d = feats[(qb, w)]; d["pass_att"] += 1; d["pass_air"] += f(r["air_yards"]); d["rz_pass"] += rz
            rec = r["receiver_player_id"]
            if rec:
                d = feats[(rec, w)]; d["tgt"] += 1; d["air"] += f(r["air_yards"]); d["rz_tgt"] += rz
            if r["qb_scramble"] == "1" and qb:
                d = feats[(qb, w)]; d["rush"] += 1; d["rz_rush"] += rz
        else:
            ru = r["rusher_player_id"]
            if ru:
                d = feats[(ru, w)]; d["rush"] += 1; d["rz_rush"] += rz
    return feats

FEATS = {"QB": ["pass_att", "pass_air", "rz_pass", "rush", "rz_rush"],
         "RB": ["rush", "rz_rush", "tgt", "rz_tgt"],
         "WR": ["tgt", "air", "rz_tgt", "rush"],
         "TE": ["tgt", "air", "rz_tgt", "rush"]}

print("parsing play-by-play ...")
F25 = pbp_features(2025)
F26 = pbp_features(2026)

COEF = {}
for pos, keys in FEATS.items():
    X, y = [], []
    for r in P[2025]:
        if r["pos"] != pos: continue
        fe = F25.get((r["pid"], r["week"]))
        if not fe: continue
        X.append([fe[k] for k in keys]); y.append(r["fp"])
    X = np.array(X); y = np.array(y)
    c, *_ = np.linalg.lstsq(X, y, rcond=None)
    COEF[pos] = dict(zip(keys, c))
    pred = X @ c
    print(f"xFP {pos}: n={len(y)} r2={1 - ((y-pred)**2).sum()/((y-y.mean())**2).sum():.2f}",
          {k: round(v, 3) for k, v in COEF[pos].items()})

for r in p26:
    if r["pos"] in FEATS:
        fe = F26.get((r["pid"], r["week"]), {})
        r["xfp"] = sum(COEF[r["pos"]][k] * fe.get(k, 0.0) for k in FEATS[r["pos"]])

# ---------------------------------------------------------------- defense / matchup factors
def pos_group_fp(rows, upto_week):
    """FP each offense scored per position group per game, keyed (team, week, pos)."""
    out = collections.defaultdict(float); opp_of = {}
    for r in rows:
        if r["week"] >= upto_week: continue
        out[(r["team"], r["week"], r["pos"])] += r["fp"]; opp_of[(r["team"], r["week"])] = r["opp"]
    return out, opp_of

def defense_factors(upto_week):
    """Opponent-adjusted FP-allowed ratio per (defense, pos), and EPA z-scores, using weeks < upto_week."""
    rows = [r for r in p26_all if r["week"] < upto_week]
    grp, opp_of = pos_group_fp(rows, upto_week)
    games_by_team = collections.defaultdict(set)
    for (t, w) in opp_of: games_by_team[t].add(w)
    ratio = {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        tot = collections.defaultdict(list)
        for (t, w), d in opp_of.items():
            val = grp.get((t, w, pos), 0.0)
            others = [grp.get((t, w2, pos), 0.0) for w2 in games_by_team[t] if w2 != w]
            if not others: continue
            base = sum(others) / len(others)
            if pos == "DST":       # DST: how many DST points does this OFFENSE (d) usually give up?
                continue
            if base > 1: tot[d].append(val / base)
        if pos == "DST":
            # factor for a DST facing offense O = O's DST-points-allowed vs the average DST's output
            dst = collections.defaultdict(list)
            dst_avg = collections.defaultdict(list)
            for r in rows:
                if r["pos"] == "DST": dst_avg[r["team"]].append(r["fp"])
            for r in rows:
                if r["pos"] != "DST": continue
                others = [x for x in dst_avg[r["team"]]]
                base = (sum(others) - r["fp"]) / max(1, len(others) - 1) if len(others) > 1 else None
                if base is None: continue
                dst[r["opp"]].append(r["fp"] - base)     # additive (DST can be ~0/neg)
            for o, l in dst.items(): ratio[(o, "DST")] = sum(l) / len(l)
        else:
            for d, l in tot.items(): ratio[(d, pos)] = sum(l) / len(l)
    # EPA allowed
    epa = collections.defaultdict(lambda: {"pass": [], "run": []})
    for r in PBP26_LITE:
        if r[0] < upto_week: epa[r[1]][r[2]].append(r[3])
    z = {}
    for kind in ("pass", "run"):
        vals = {t: sum(v[kind]) / len(v[kind]) for t, v in epa.items() if v[kind]}
        mu = sum(vals.values()) / len(vals); sd = (sum((x - mu) ** 2 for x in vals.values()) / len(vals)) ** .5
        for t, x in vals.items(): z[(t, kind)] = (x - mu) / sd
    return ratio, z

PBP26_LITE = []
for r in load_csv("play_by_play_2026.csv.gz"):
    if r["season_type"] == "REG" and r["play_type"] in ("pass", "run") and r["epa"] != "" and r["defteam"]:
        PBP26_LITE.append((int(r["week"]), r["defteam"], r["play_type"], f(r["epa"])))

def matchup_mult(ratio, z, defense, pos, shrink):
    if pos == "DST":
        add = ratio.get((defense, "DST"), 0.0)       # points above average a DST scores vs this offense
        return None, add * shrink
    raw = ratio.get((defense, pos), 1.0)
    if pos == "RB": zq = 0.6 * z.get((defense, "run"), 0) + 0.4 * z.get((defense, "pass"), 0)
    elif pos == "K": zq = 0.5 * z.get((defense, "run"), 0) + 0.5 * z.get((defense, "pass"), 0)
    else: zq = z.get((defense, "pass"), 0)
    m = 0.6 * raw + 0.4 * (1 + 0.08 * zq)
    return clamp(1 + shrink * (m - 1), 0.8, 1.2), None

# ---------------------------------------------------------------- baseline components
XREC = float(os.environ.get("XREC", "0.7"))
def components(rows, upto_week):
    """per player: S (season avg), L (last-4 avg), X (xFP avg), gp, using weeks < upto_week."""
    by = collections.defaultdict(list)
    for r in rows:
        if r["week"] < upto_week: by[r["pid"]].append(r)
    out = {}
    for pid, l in by.items():
        l.sort(key=lambda r: r["week"])
        S = sum(r["fp"] for r in l) / len(l)
        L = sum(r["fp"] for r in l[-4:]) / len(l[-4:])
        X = None
        if l[0]["pos"] in FEATS:
            w = [XREC ** (len(l) - 1 - i) for i in range(len(l))]     # exponential recency weighting
            X = sum(wi * r.get("xfp", 0) for wi, r in zip(w, l)) / sum(w)
        out[pid] = dict(S=S, L=L, X=X, gp=len(l), pos=l[0]["pos"], team=l[-1]["team"], name=l[-1]["name"])
    return out

# ---------------------------------------------------------------- backtest (rolling, weeks 6..LAST_WEEK)
print("backtesting weights ...")
grid = []
for wx in (0.2, 0.3, 0.35, 0.4, 0.5):
    for wl in (0.15, 0.25, 0.35):
        ws = round(1 - wx - wl, 2)
        if ws < 0.1: continue
        grid.append((wx, wl, ws))
results = {}
cache = {}
for wk in range(6, LAST_WEEK + 1):
    cache[wk] = (components(p26_all, wk), defense_factors(wk))
for shrink in (0.0, 0.25, 0.5, 0.75):
    for (wx, wl, ws) in grid:
        err = []; resid = []
        for wk in range(6, LAST_WEEK + 1):
            comp, (ratio, z) = cache[wk]
            for r in p26_all:
                if r["week"] != wk or r["pos"] not in SKILL: continue
                c = comp.get(r["pid"])
                if not c or c["gp"] < 3: continue
                X = c["X"] if c["X"] is not None else c["S"]
                base = wx * X + wl * c["L"] + ws * c["S"]
                if base < 3: continue                      # ignore deep bench noise
                m, _ = matchup_mult(ratio, z, r["opp"], r["pos"], shrink)
                pred = base * m
                err.append(abs(pred - r["fp"])); resid.append((r["pos"], pred, r["fp"] - pred))
        results[(shrink, wx, wl, ws)] = (sum(err) / len(err), resid)
best = min(results, key=lambda k: results[k][0])
for sh in (0.0, 0.25, 0.5, 0.75):
    k=(sh,)+best[1:]; print("  shrink", sh, "MAE %.3f" % results[k][0])
for k in sorted(results, key=lambda k: results[k][0])[:6]: print("  top", k, "%.3f" % results[k][0])
nomatch = min((k for k in results if k[0] == 0.0), key=lambda k: results[k][0])
SHRINK, WX, WL, WS = best
print("best", best, "MAE %.3f" % results[best][0], "| best w/o matchup", nomatch, "MAE %.3f" % results[nomatch][0])
default_key = (0.5, 0.35, 0.25, 0.4)
if default_key in results: print("planned default (0.5 shrink, 35/25/40) MAE %.3f" % results[default_key][0])

# SD model per position: sd = a + b*mean, fit on backtest residuals
SDM = {}
resid = results[best][1]
for pos in SKILL:
    pts = [(p, e) for (ps, p, e) in resid if ps == pos]
    pts.sort()
    bins = [pts[i:i + 60] for i in range(0, len(pts), 60)]
    xs = [sum(p for p, _ in b) / len(b) for b in bins if len(b) > 20]
    ys = [(sum(e * e for _, e in b) / len(b)) ** .5 for b in bins if len(b) > 20]
    b, a = np.polyfit(xs, ys, 1) if len(xs) > 2 else (0.5, 2.0)
    SDM[pos] = (float(max(a, 1.5)), float(clamp(b, 0.25, 0.9)))
SDM["K"] = (3.0, 0.25); SDM["DST"] = (4.5, 0.15)
print("sd model", {k: (round(a, 2), round(b, 2)) for k, (a, b) in SDM.items()})

# Matchup strength: backtest says 0.25 costs ~nothing vs 0 (and the user wants defense weighed),
# so use 0.25 as the default; the page exposes a slider for it.
SHRINK = 0.25
chosen = (SHRINK, WX, WL, WS)
BACKTEST = dict(mae=round(results[chosen][0], 3), best_mae=round(results[best][0], 3), mae_no_matchup=round(results[nomatch][0], 3),
                weights=dict(xfp=WX, last4=WL, season=WS), shrink=SHRINK,
                planned_mae=round(results[default_key][0], 3) if default_key in results else None,
                weeks=f"6-{LAST_WEEK}", n=len(resid))

# ---------------------------------------------------------------- full-season components for projection
COMP = components(p26_all, 99)
RATIO, Z = defense_factors(99)

# defense rank text for display (rank 1 = toughest)
DEF_RANK = {}
for pos in ("QB", "RB", "WR", "TE", "K"):
    vals = sorted(((matchup_mult(RATIO, Z, t, pos, 1.0)[0], t) for t in ESPN_TEAM.values()))
    for i, (_, t) in enumerate(vals): DEF_RANK[(t, pos)] = i + 1
vals = sorted(((RATIO.get((t, "DST"), 0), t) for t in ESPN_TEAM.values()))
for i, (_, t) in enumerate(vals): DEF_RANK[(t, "DST")] = i + 1   # rank 1 = offense that gives DSTs least

# ---------------------------------------------------------------- schedule, team totals, environment
SCHED = collections.defaultdict(dict)    # team -> week -> dict(opp, home, total, spread)
for g in g26:
    w = int(g["week"])
    if w not in PROJ_WEEKS: continue
    tot = f(g["total_line"]) if g["total_line"] else None
    sp = f(g["spread_line"]) if g["spread_line"] else None
    h, a = g["home_team"], g["away_team"]
    vh = va = None
    if tot is not None and sp is not None:
        vh, va = (tot + sp) / 2, (tot - sp) / 2
    final = g["home_score"] != ""
    SCHED[h][w] = dict(opp=a, home=True, vegas=vh, final=final, day=g["weekday"], gid=g["game_id"])
    SCHED[a][w] = dict(opp=h, home=False, vegas=va, final=final, day=g["weekday"], gid=g["game_id"])

pts_for = collections.defaultdict(list); pts_against = collections.defaultdict(list)
for (t, w), (pf, pa) in score.items():
    pts_for[t].append(pf); pts_against[t].append(pa)
LG = sum(sum(v) for v in pts_for.values()) / sum(len(v) for v in pts_for.values())

def model_total(team, opp, home):
    o = sum(pts_for[team]) / len(pts_for[team]); d = sum(pts_against[opp]) / len(pts_against[opp])
    return LG * (1 + 0.6 * (o / LG - 1)) * (1 + 0.6 * (d / LG - 1)) + (1.0 if home else -1.0)

def environment(team, w):
    s = SCHED[team].get(w)
    if not s: return 1.0, None, None
    mt = model_total(team, s["opp"], s["home"])
    if s["vegas"]:
        return clamp((s["vegas"] / mt) ** 0.6, 0.85, 1.15), round(s["vegas"], 1), round(mt, 1)
    return (1.01 if s["home"] else 0.99), None, round(mt, 1)

# ---------------------------------------------------------------- history vs opponent
HIST = collections.defaultdict(list)    # (pid, opp) -> list of ratios
for season in (2023, 2024, 2025, 2026):
    rows = P[season]
    avg = collections.defaultdict(list)
    for r in rows: avg[r["pid"]].append(r["fp"])
    for r in rows:
        a = sum(avg[r["pid"]]) / len(avg[r["pid"]])
        if a >= 4: HIST[(r["pid"], r["opp"])].append((season, r["week"], r["fp"], r["fp"] / a))

def history(pid, opp):
    l = HIST.get((pid, opp), [])
    if len(l) < 2: return 1.0, l
    mr = sum(x[3] for x in l) / len(l)
    return clamp(1 + 0.25 * (mr - 1) * len(l) / (len(l) + 4), 0.95, 1.05), l

# ---------------------------------------------------------------- ESPN league
league = json.load(open(os.path.join(ESPN, "league.json")))
fa = json.load(open(os.path.join(ESPN, "fa.json")))

players_csv = load_csv("players.csv")
ESPN2GSIS = {r["espn_id"]: r["gsis_id"] for r in players_csv if r["espn_id"]}
NAME2GSIS = collections.defaultdict(list)
for pid, c in COMP.items(): NAME2GSIS[c["name"].lower().replace(".", "").replace("'", "")].append(pid)

def espn_stat(p, period, source, split=None):
    for s in p.get("stats", []):
        if s.get("seasonId") == SEASON and s["scoringPeriodId"] == period and s["statSourceId"] == source \
                and (split is None or s["statSplitTypeId"] == split):
            return s.get("appliedTotal")
    return None

INJ_AVAIL = {  # availability by status for [this week, next week, later]
    "ACTIVE": (1, 1, 1), None: (1, 1, 1), "NORMAL": (1, 1, 1),
    "QUESTIONABLE": (0.85, 0.97, 1), "DOUBTFUL": (0.25, 0.8, 0.95), "OUT": (0.0, 0.6, 0.9),
    "SUSPENSION": (0.0, 0.0, 0.5), "INJURY_RESERVE": (0.0, 0.0, 0.0),
}

def build_player(p, fteam, slot):
    pos = POS.get(p["defaultPositionId"])
    if not pos: return None
    nfl = ESPN_TEAM.get(p.get("proTeamId"))
    eid = str(p["id"])
    if pos == "DST": gsis = "DST_" + (nfl or "")
    else:
        gsis = ESPN2GSIS.get(eid)
        if not gsis or gsis not in COMP:
            cand = NAME2GSIS.get(p["fullName"].lower().replace(".", "").replace("'", ""), [])
            if len(cand) == 1: gsis = cand[0]
    c = COMP.get(gsis)
    espn_season_actual0 = espn_stat(p, 0, 0, 0)
    if pos == "DST" and c and espn_season_actual0:
        # calibrate our D/ST scoring to ESPN's official totals (return TDs, PA rules differ slightly)
        off = (espn_season_actual0 - c["S"] * c["gp"]) / c["gp"]
        c = dict(c, S=c["S"] + off, L=c["L"] + off)
    inj = p.get("injuryStatus")
    if inj == "ACTIVE" or inj == "NORMAL": inj = None

    e11 = espn_stat(p, CUR_WEEK, 1, 1)
    eseason = espn_stat(p, 0, 1, 0)
    actual11 = espn_stat(p, CUR_WEEK, 0, 1)
    espn_season_actual = espn_stat(p, 0, 0, 0)

    # ESPN baseline: strip ESPN's week-11 matchup context using our own factors
    e_base = None
    s11 = SCHED.get(nfl, {}).get(CUR_WEEK) if nfl else None
    if e11 and e11 > 0.5 and s11:
        if pos == "DST":
            e_base = e11 - (RATIO.get((s11["opp"], "DST"), 0) * SHRINK)
        else:
            m, _ = matchup_mult(RATIO, Z, s11["opp"], pos, SHRINK)
            env, _, _ = environment(nfl, CUR_WEEK)
            e_base = e11 / (m * env)

    # blend baseline
    parts = {}
    if c:
        X = c["X"] if (c["X"] is not None) else None
        rel = min(1.0, c["gp"] / 4)                      # trust our data less with < 4 games
        comp_w = []
        if X is not None: comp_w += [("xfp", X, WX)]; comp_w += [("last4", c["L"], WL), ("season", c["S"], WS)]
        else: comp_w += [("last4", c["L"], 0.4), ("season", c["S"], 0.6)]
        ours = sum(v * w for _, v, w in comp_w) / sum(w for _, _, w in comp_w)
        w_espn = 0.15 if e_base is not None else 0.0
        if e_base is not None and rel < 1: w_espn = 0.15 + 0.6 * (1 - rel)
        base = (1 - w_espn) * ours + w_espn * (e_base if e_base is not None else 0)
        parts = {k: round(v, 2) for k, v, _ in comp_w}
        parts["espn"] = round(e_base, 2) if e_base is not None else None
        parts["w_espn"] = round(w_espn, 2)
        gp = c["gp"]
    else:
        base = e_base or 0.0
        parts = {"espn": round(e_base, 2) if e_base is not None else None, "w_espn": 1.0}
        gp = 0

    a_now, a_next, a_later = INJ_AVAIL.get(inj, (1, 1, 1))
    sd_a, sd_b = SDM[pos]
    weeks = {}
    for i, w in enumerate(PROJ_WEEKS):
        s = SCHED.get(nfl, {}).get(w) if nfl else None
        if not s:
            weeks[w] = dict(mean=0, sd=0, bye=True)
            continue
        opp = s["opp"]
        if w == CUR_WEEK and s["final"]:
            weeks[w] = dict(mean=round(actual11 or 0, 2), sd=0, locked=True, opp=opp, home=s["home"])
            continue
        avail = a_now if w == CUR_WEEK else (a_next if w == CUR_WEEK + 1 else a_later)
        if pos == "DST":
            _, add = matchup_mult(RATIO, Z, opp, pos, SHRINK)
            opp_env, _, _ = environment(opp, w)
            env = 1.0
            mean = max(0.0, (base + add) * (2 - opp_env)) * avail
            m = None; h = 1.0; hl = []
        else:
            m, _ = matchup_mult(RATIO, Z, opp, pos, SHRINK)
            env, vegas, mt = environment(nfl, w)
            h, hl = history(gsis, opp) if gsis else (1.0, [])
            mean = base * m * env * h * avail
            add = None
        sd = (sd_a + sd_b * mean) if mean > 0 else 0
        mraw = matchup_mult(RATIO, Z, opp, pos, 1.0)
        rec = dict(mean=round(mean, 2), sd=round(sd, 2), opp=opp, home=s["home"],
                   mraw=round(mraw[0], 4) if mraw[0] else None,
                   addraw=round(mraw[1], 3) if mraw[1] is not None else None,
                   oenv=round(environment(opp, w)[0], 3) if pos == "DST" else None,
                   m=round(m, 3) if m else None, env=round(env, 3), h=round(h, 3), a=avail,
                   rank=DEF_RANK.get((opp, pos)))
        if add is not None: rec["add"] = round(add, 2)
        if s.get("vegas") and pos != "DST": rec["vegas"] = round(s["vegas"], 1)
        if hl: rec["hist"] = [[x[0], x[1], round(x[2], 1)] for x in hl[-4:]]
        weeks[w] = rec
    return dict(id=eid, gsis=gsis, name=p["fullName"], pos=pos, nfl=nfl, inj=inj, fteam=fteam, slot=slot,
                base=round(base, 2), parts=parts, gp=gp,
                ppg=round(c["S"], 2) if c else None, last4=round(c["L"], 2) if c else None,
                xfp=round(c["X"], 2) if c and c["X"] is not None else None,
                espn11=round(e11, 2) if e11 is not None else None,
                espnSeasonPts=round(espn_season_actual, 1) if espn_season_actual is not None else None,
                own=round(p.get("ownership", {}).get("percentOwned", 0), 1),
                weeks=weeks)

players = {}
unmatched = []
for t in league["teams"]:
    for e in t["roster"]["entries"]:
        p = e["playerPoolEntry"]["player"]
        bp = build_player(p, t["id"], SLOT.get(e["lineupSlotId"], "BN"))
        if bp:
            players[bp["id"]] = bp
            if bp["gp"] == 0 and bp["pos"] != "DST": unmatched.append((bp["name"], bp["nfl"]))
for e in fa["players"]:
    p = e["player"]
    if str(p["id"]) in players: continue
    bp = build_player(p, 0, "FA")
    if bp: players[bp["id"]] = bp
print("players", len(players), "rostered w/o 2026 games:", unmatched)

# validation: our season FP vs ESPN's season actuals (rostered players)
diffs = []
for bp in players.values():
    if bp["fteam"] and bp["espnSeasonPts"] and bp["gp"]:
        ours = bp["ppg"] * bp["gp"]
        diffs.append((abs(ours - bp["espnSeasonPts"]), bp["name"], round(ours, 1), bp["espnSeasonPts"]))
diffs.sort(reverse=True)
print("largest season-total diffs vs ESPN:", diffs[:8])
print("median diff:", sorted(d[0] for d in diffs)[len(diffs) // 2] if diffs else None)

# ---------------------------------------------------------------- teams / schedule
teams = []
for t in league["teams"]:
    r = t["record"]["overall"]
    teams.append(dict(id=t["id"], name=t["name"].strip(), abbrev=t.get("abbrev"), w=r["wins"], l=r["losses"],
                      t=r["ties"], pf=round(r["pointsFor"], 2), pa=round(r["pointsAgainst"], 2),
                      seed=t.get("playoffSeed")))
schedule = []
for m in league["schedule"]:
    if m["matchupPeriodId"] in REG_WEEKS:
        schedule.append(dict(week=m["matchupPeriodId"], home=m["home"]["teamId"], away=m["away"]["teamId"]))

nfl_sched = {t: {w: [s["opp"], s["home"], s["final"]] for w, s in d.items()} for t, d in SCHED.items()}
byes = {w: sorted(t for t in ESPN_TEAM.values() if w not in SCHED[t]) for w in PROJ_WEEKS}

data = dict(
    meta=dict(league=league["settings"]["name"], season=SEASON, week=CUR_WEEK, myTeam=MY_TEAM,
              generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              statsThrough=LAST_WEEK, scoring="Full PPR (ESPN standard)",
              lineup=dict(QB=1, RB=2, WR=2, TE=1, FLEX=1, K=1, DST=1, BN=7, IR=1),
              playoffs=dict(teams=4, semis=[15], final=[16, 17], seeding="Record, then total points for"),
              tradeDeadline="2026-12-02", backtest=BACKTEST,
              sdModel=SDM, shrink=SHRINK, weights=dict(xfp=WX, last4=WL, season=WS)),
    teams=teams, schedule=schedule, players=list(players.values()), nflSchedule=nfl_sched, byes=byes)
json.dump(data, open(OUT, "w"), separators=(",", ":"))
print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")
