"""Compare actual archived Git revisions of as-of CBB team intelligence.

Unlike a historical backtest reconstructed from today's source archive, a
Git revision is a real, dated record of what the research system saved.
Report profile movement only; do not infer past sportsbook performance.
"""
import argparse
import json
import math
import subprocess
from pathlib import Path

from cbb import dt, iso, utcnow

PATH = "data/advanced/team_intelligence_{year}.json"
NUMERIC = ("opponent_adjusted_strength_points",
           "schedule_strength_avg_opponent_points",
           "last5_opponent_adjusted_margin",
           "strength_of_record_wins_above_expected",
           "off_rtg_for_adj", "off_rtg_allowed_adj",
           "efg_pct_for_adj", "efg_pct_allowed_adj",
           "tov_pct_for_adj", "tov_pct_allowed_adj")
RANKING = "opponent_adjusted_strength_points"


def dated_snapshots(repo, path, max_revisions=90):
    """Read actual committed source revisions, not reconstruction."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "log", f"-n{max_revisions}",
             "--format=%H", "--", path], text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return []
    result = []
    seen = set()
    for sha in out.splitlines():
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"{sha}:{path}"],
                text=True, stderr=subprocess.DEVNULL)
            doc = json.loads(raw)
            when = doc["as_of_utc"]
            if when in seen or not isinstance(doc.get("teams"), dict):
                continue
            seen.add(when)
            result.append((when, sha, doc))
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError):
            continue
    return sorted(result, key=lambda r: r[0])


def rank(teams):
    sorted_ids = sorted((str(t) for t, p in teams.items()
                         if p.get(RANKING) is not None),
                        key=lambda t: (-float(teams[t][RANKING]), t))
    return {team: i + 1 for i, team in enumerate(sorted_ids)}


def compare(old, latest, older_sha, min_gap_hours=12):
    old_time, latest_time = dt(old["as_of_utc"]), dt(latest["as_of_utc"])
    if latest_time <= old_time or (latest_time - old_time).total_seconds() < min_gap_hours * 3600:
        return {"status": "INSUFFICIENT_SAVED_PROFILE_HISTORY",
                "min_history_hours": min_gap_hours}
    before, after = old["teams"], latest["teams"]
    old_rank, new_rank = rank(before), rank(after)
    all_teams = set(before) & set(after)
    changes = []
    for tid in all_teams:
        a, b = before[tid], after[tid]
        strength_old, strength_new = a.get(RANKING), b.get(RANKING)
        if strength_old is None or strength_new is None:
            continue
        changes.append({
            "team_id": tid, "rank_old": old_rank.get(tid),
            "rank_latest": new_rank.get(tid),
            "old_d1_games": a.get("current_d1_games"),
            "latest_d1_games": b.get("current_d1_games"),
            "strength_old": float(strength_old),
            "strength_latest": float(strength_new),
            "strength_change_points": round(float(strength_new)-float(strength_old), 3),
            "profile_changes": {
                f: round(float(b[f])-float(a[f]), 4)
                for f in NUMERIC
                if a.get(f) is not None and b.get(f) is not None},
        })
    increases = sorted(changes, key=lambda r: (-r["strength_change_points"], r["team_id"]))
    decreases = sorted(changes, key=lambda r: (r["strength_change_points"], r["team_id"]))
    movements = [abs(x["strength_change_points"]) for x in changes]
    return {
        "status": "SAVED_AS_OF_PROFILE_COMPARISON",
        "old_profile_as_of_utc": old["as_of_utc"],
        "latest_profile_as_of_utc": latest["as_of_utc"],
        "old_profile_git_commit": older_sha,
        "source": "Actual previously committed git revision versus current saved profile",
        "min_history_hours": min_gap_hours,
        "team_ids_compared": len(changes),
        "mean_abs_strength_change_points": (
            round(sum(movements)/len(movements), 4) if movements else None),
        "teams_rising_most": increases[:15],
        "teams_falling_most": decreases[:15],
        "all_team_changes": sorted(changes, key=lambda r: r["team_id"]),
        "caveat": ("Observed profile drift does not prove improved pre-tip "
                   "predictions. A profile from November 13 is forbidden "
                   "when grading games from November 10."),
    }


def build_audit(root="data", year=2027, repo=".", oldest_min_hours=18):
    root = Path(root)
    repo = Path(repo)
    rel = PATH.format(year=year)
    path = root/"advanced"/f"team_intelligence_{year}.json"
    if not path.exists():
        return {"status": "NO_CURRENT_PROFILE"}
    latest = json.loads(path.read_text(encoding="utf-8"))
    revisions = dated_snapshots(repo, rel)
    if not revisions:
        return {"status": "NO_AUTHENTIC_GIT_HISTORY", "new_profile_as_of_utc": latest.get("as_of_utc")}
    candidates = [(t, sha, doc) for t, sha, doc in revisions
                  if (dt(latest["as_of_utc"])-dt(t)).total_seconds() >= oldest_min_hours*3600]
    if not candidates:
        return {
            "status": "INSUFFICIENT_SAVED_PROFILE_HISTORY",
            "new_profile_as_of_utc": latest["as_of_utc"],
            "earliest_archived_utc": revisions[0][0],
            "revisions_found": len(revisions)}
    # Closest actual saved version at least 18 hours older; never use a
    # retrospectively recomputed document labeled as a genuine old capture.
    when, sha, earlier = max(candidates, key=lambda e: e[0])
    report = compare(earlier, latest, sha, min_gap_hours=oldest_min_hours)
    report["archived_git_revisions_checked"] = len(revisions)
    return report


def raw_profile_revisions(repo, year=2027, max_revisions=60):
    """Actual Git-stored raw boxscore profiles with commit timestamps.

    Raw team profile JSON has no embedded as-of time. Commit timestamps are
    evidence of when files were archived, NOT evidence of when ESPN first
    published every individual source statistic.
    """
    path=f"data/advanced/team_profiles_{year}.json"
    try:
        result=subprocess.check_output(
            ["git","-C",str(repo),"log",f"-n{max_revisions}",
             "--format=%H|%cI","--",path],
            text=True,stderr=subprocess.DEVNULL)
    except (OSError,subprocess.CalledProcessError):
        return []
    rows=[]
    for line in result.splitlines():
        if "|" not in line:
            continue
        sha, committed=line.strip().split("|",1)
        try:
            content=subprocess.check_output(
                ["git","-C",str(repo),"show",f"{sha}:{path}"],
                text=True,stderr=subprocess.DEVNULL)
            teams=json.loads(content)
            if not isinstance(teams,dict) or not teams:
                continue
            rows.append((dt(committed),sha,teams))
        except (OSError,ValueError,subprocess.CalledProcessError):
            continue
    return sorted(rows,key=lambda x:x[0])


def raw_saved_profile_comparison(repo=".", year=2027, min_gap_hours=36):
    """Compare raw team metrics at two genuinely saved commits, >=36h apart."""
    revisions=raw_profile_revisions(repo,year)
    if not revisions:
        return {"status":"NO_SAVED_RAW_PROFILE_HISTORY"}
    latest_time,latest_sha,latest=revisions[-1]
    candidates=[x for x in revisions[:-1]
                if (latest_time-x[0]).total_seconds()>=min_gap_hours*3600]
    if not candidates:
        return {"status":"INSUFFICIENT_SAVED_RAW_PROFILE_HISTORY",
                "newest_git_commit":latest_sha,
                "revisions_seen":len(revisions)}
    old_time,old_sha,old=max(candidates,key=lambda x:x[0])
    common=set(old)&set(latest)
    names=("off_rtg","pace_est","efg_pct","tov_pct","orb_pct","ft_rate","three_rate")
    compared=[]
    for tid in common:
        before,after=old[tid],latest[tid]
        try:
            ng,og=int(after["games"]),int(before["games"])
            if ng<3 or og<2:
                continue
            deltas={}
            for field in names:
                v1,v2=before.get(field),after.get(field)
                if v1 is None or v2 is None:
                    continue
                delta=float(v2)-float(v1)
                if math.isfinite(delta):
                    deltas[field]=round(delta,5)
            if "off_rtg" not in deltas:
                continue
            compared.append({
                "team_id":str(tid),
                "old_observed_games":og,"latest_observed_games":ng,
                "old_raw_off_rtg":before["off_rtg"],
                "latest_raw_off_rtg":after["off_rtg"],
                "raw_stat_deltas":deltas,
            })
        except (KeyError,TypeError,ValueError,OverflowError):
            continue
    increases=sorted(compared,key=lambda x:(-x["raw_stat_deltas"]["off_rtg"],x["team_id"]))
    decreases=sorted(compared,key=lambda x:(x["raw_stat_deltas"]["off_rtg"],x["team_id"]))
    n=len(compared)
    return {
        "status":"AUTHENTIC_GIT_SAVED_RAW_PROFILE_DRIFT",
        "old_commit":old_sha,"new_commit":latest_sha,
        "old_git_committed_utc":iso(old_time),
        "new_git_committed_utc":iso(latest_time),
        "teams_old":len(old),"teams_new":len(latest),
        "teams_in_both":len(common),
        "teams_with_old2_new3plus":n,
        "average_abs_raw_off_rtg_change":(
            round(sum(abs(x["raw_stat_deltas"]["off_rtg"])
                      for x in compared)/n,4) if n else None),
        "biggest_increases":increases[:12],
        "biggest_decreases":decreases[:12],
        "all_team_changes":sorted(compared,key=lambda x:x["team_id"]),
        "caveat":("These are raw team boxscore aggregates. They are NOT "
                  "opponent-adjusted ratings, and profile movement is NOT "
                  "predictive error improvement. Git timestamps mark saved "
                  "commits; individual upstream ESPN publication times "
                  "are not guaranteed by this comparison.")
    }


def run(root="data", repo=".", year=2027):
    report = build_audit(root=root, repo=repo, year=year)
    report["raw_saved_profile_history"] = raw_saved_profile_comparison(
        repo=repo,year=year,min_gap_hours=36)
    target = Path(root)/"reports"/f"profile_history_audit_{year}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n",
                      encoding="utf-8")
    return {"status": report["status"], "compared_teams": report.get("team_ids_compared", 0),
            "old_as_of": report.get("old_profile_as_of_utc"),
            "latest_as_of": report.get("latest_profile_as_of_utc"),
            "raw_history_status":report["raw_saved_profile_history"]["status"],
            "raw_teams_compared":report["raw_saved_profile_history"].get(
                "teams_with_old2_new3plus",0)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--season", type=int, default=2027)
    args = ap.parse_args()
    print(json.dumps(run(args.root, args.repo, args.season), indent=2))
