"""CBB historical box-score coverage and deterministic priority backfill.

This reports *observed research coverage*, not historical as-of availability.
No part of this module changes published score predictions.
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from cbb import dt, iso, read_csv, utcnow
from team_directory import load_directory


def complete_before(g, now):
    try:
        return (str(g.get("score_conflict","0")) not in ("1","True","true")
                and str(g.get("completed")) in ("1", "True", "true")
                and g.get("home_score") not in ("", None)
                and g.get("away_score") not in ("", None)
                and dt(g["tip_utc"]) + timedelta(hours=4) <= now
                and str(g["home_id"]) != str(g["away_id"]))
    except (ValueError, KeyError, TypeError):
        return False


def select_games(rows, accepted_ids, now, max_games=48,
                 d1_ids=None, previous_quota=28, prior_quota=8):
    """Reserve current, last and prior season slots; backfill oldest-first.

    Scoreboard-only names/IDs from unknown divisions are excluded if an
    independently fetched D-I directory is available. A known-empty directory
    blocks selection rather than silently treating everything as D-I.
    """
    if max_games < 1 or previous_quota < 0 or prior_quota < 0:
        raise ValueError("invalid request budget")
    ids = None if d1_ids is None else {str(x) for x in d1_ids}
    if ids is not None and not ids:
        return []
    seen = {str(x) for x in accepted_ids}
    current = now.year + int(now.month >= 7)
    pools = defaultdict(list)
    for g in rows:
        if not complete_before(g, now) or str(g["game_id"]) in seen:
            continue
        if ids is not None and (str(g["home_id"]) not in ids or str(g["away_id"]) not in ids):
            continue
        year = int(g["season"])
        if year in (current, current - 1, current - 2):
            pools[year].append(g)
    # Recent season: latest first for rapidly evolving team profiles.
    pools[current].sort(key=lambda x:(x["tip_utc"], str(x["game_id"])), reverse=True)
    # History: earliest first to accumulate a coherent chronological foundation.
    for year in (current - 1, current - 2):
        pools[year].sort(key=lambda x:(x["tip_utc"], str(x["game_id"])))
    older_cap = min(prior_quota, max_games)
    previous_cap = min(previous_quota, max_games - older_cap)
    current_cap = max_games - previous_cap - older_cap
    chosen, chosen_ids = [], set()
    def add(items):
        for g in items:
            k = str(g["game_id"])
            if len(chosen) >= max_games:
                break
            if k not in chosen_ids:
                chosen.append(g)
                chosen_ids.add(k)
    add(pools[current][:current_cap])
    add(pools[current - 1][:previous_cap])
    add(pools[current - 2][:older_cap])
    # Reallocate unused quota without requiring another workflow or extra calls.
    for y in (current, current - 1, current - 2):
        add(pools[y])
    return chosen


def audit(rows, accepted, now, d1_ids):
    """Validate joins and report explicit *eligible* denominators per season."""
    d1 = {str(x) for x in d1_ids}
    current = now.year + int(now.month >= 7)
    seasons = (current - 2, current - 1, current)
    eligible = {}
    other, disputed, disputed_ids = Counter(), Counter(), set()
    for g in rows:
        if int(g["season"]) not in seasons:
            continue
        gid = str(g["game_id"])
        if str(g.get("score_conflict","0")) in ("1","True","true"):
            disputed[int(g["season"])] += 1
            disputed_ids.add(gid)
            continue
        if not complete_before(g, now):
            continue
        if str(g["home_id"]) in d1 and str(g["away_id"]) in d1:
            if gid in eligible:
                raise ValueError("duplicate scoreboard ID " + gid)
            eligible[gid] = g
        else:
            other[int(g["season"])] += 1
    valid, rejected = {}, []
    disputed_archives = 0
    team_games = defaultdict(Counter)
    for key, rec in accepted.items():
        gid = str(key)
        if gid in disputed_ids:
            disputed_archives += 1
            continue  # Already quarantined at source; never count as verified.
        g = eligible.get(gid)
        problems = []
        if g is None:
            problems.append("no matching eligible D-I final")
        else:
            if str(rec.get("game_id")) != gid:
                problems.append("record game ID mismatch")
            if rec.get("tip_utc") != g["tip_utc"]:
                problems.append("tip mismatch")
            if int(rec.get("season", -1)) != int(g["season"]):
                problems.append("season mismatch")
            if (str(rec.get("home_id")),str(rec.get("away_id"))) != (str(g["home_id"]),str(g["away_id"])):
                problems.append("team identity mismatch")
            try:
                if (float(rec["home"]["points"]),float(rec["away"]["points"])) != (float(g["home_score"]),float(g["away_score"])):
                    problems.append("score mismatch")
                if dt(rec["source_captured_utc"]) < dt(g["tip_utc"]):
                    problems.append("invalid capture timestamp")
                sha = str(rec["raw_sha256"])
                if len(sha)!=64 or any(c not in "0123456789abcdef" for c in sha.lower()):
                    problems.append("missing full source hash")
            except (ValueError,KeyError,TypeError):
                problems.append("incomplete evidence")
        if problems:
            rejected.append({"game_id":gid,"reason":"; ".join(problems)})
        elif gid in valid:
            rejected.append({"game_id":gid,"reason":"duplicate accepted game"})
        else:
            valid[gid] = rec
            team_games[int(g["season"])][str(g["home_id"])] += 1
            team_games[int(g["season"])][str(g["away_id"])] += 1
    by_season = {}
    for year in seasons:
        possible = sum(int(g["season"]) == year for g in eligible.values())
        captured = sum(int(rec["season"]) == year for rec in valid.values())
        count = team_games[year]
        by_season[str(year)] = {
            "eligible_d1_final_games":possible,
            "verified_boxscores":captured,
            "missing_boxscores":possible - captured,
            "coverage_fraction":round(captured/possible,4) if possible else None,
            "other_or_unverified_final_games":other[year],
            "disputed_source_score_games":disputed[year],
            "d1_teams_with_5plus_games":sum(v>=5 for v in count.values()),
            "teams_with_any_verified_games":len(count),
        }
    if not d1:
        state = "BLOCKED_NO_VERIFIED_TEAM_DIRECTORY"
    elif rejected:
        state = "QUARANTINED_SOURCE_MISMATCHES"
    elif disputed_ids:
        state = "AUDITED_WITH_QUARANTINED_SCORE_REVISIONS"
    else:
        state = "AUDITED_PARTIAL_COVERAGE"
    return {
        "schema":1,"status":state,"as_of_utc":iso(now),
        "source":"ESPN scoreboard + ESPN game summary + independent ESPN team directory",
        "verified_d1_directory_count":len(d1),
        "accepted_rows_in_archive":len(accepted),
        "eligible_verified_d1_boxscores":len(valid),
        "quarantined_rows":len(rejected),"quarantine_sample":rejected[:20],
        "disputed_source_score_games":len(disputed_ids),
        "disputed_archived_boxscores":disputed_archives,
        "by_season":by_season,
        "historical_available_as_of":False,
        "active_model_changed":False,
        "warning":"Retrospective statistics cannot be treated as if acquired before past games. No unsupported odds or player availability."
    }


def run_audit(root="data", now=None):
    now=now or utcnow()
    root=Path(root)
    from boxscore_features import load_accepted
    accepted=load_accepted(root/"advanced/boxscore_team_games.jsonl")
    try:
        teams=load_directory(root/"team_directory.json")
    except (ValueError,OSError):
        teams={}
    result=audit(read_csv(root/"games.csv"),accepted,now,teams)
    out=root/"advanced/coverage_audit.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default="data")
    args=parser.parse_args()
    result=run_audit(args.root)
    print(json.dumps(result,indent=2))
    if result["status"]=="QUARANTINED_SOURCE_MISMATCHES":
        raise SystemExit("Source mismatch: quarantine audit must be resolved before model experiments")
