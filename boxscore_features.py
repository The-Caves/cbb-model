"""Audited free ESPN men's college-basketball team box score ingestion.

RESEARCH DATA ONLY. No active forecast reads these features. Historical box
scores fetched today were NOT necessarily available at past decision times.
"""
import argparse
import gzip
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from cbb import dt, iso, read_csv, utcnow

SUMMARY_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event="
PAIRS = {
    "fg": "fieldGoalsMade-fieldGoalsAttempted",
    "three": "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
    "ft": "freeThrowsMade-freeThrowsAttempted",
}

class BoxscoreGap(ValueError):
    """A missing or contradictory source must not become estimated facts."""


def number(value, key):
    if value is None or str(value).strip() == "":
        raise BoxscoreGap("missing " + key)
    try:
        v = float(str(value).replace(",", ""))
    except (ValueError, TypeError) as exc:
        raise BoxscoreGap("invalid " + key) from exc
    if not math.isfinite(v) or v < 0:
        raise BoxscoreGap("out-of-range " + key)
    return v


def pair(value, key):
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(value))
    if not m:
        raise BoxscoreGap("missing/invalid " + key)
    made, attempted = int(m.group(1)), int(m.group(2))
    if made > attempted:
        raise BoxscoreGap("made exceeds attempts: " + key)
    return made, attempted


def decode_side(entry):
    stats = {}
    for rec in entry.get("statistics") or []:
        if not isinstance(rec, dict):
            continue
        key = rec.get("name")
        if key:
            value = rec.get("displayValue")
            if value in (None, ""):
                value = rec.get("value")
            if key in stats and stats[key] != value:
                raise BoxscoreGap("conflicting statistic: " + key)
            stats[key] = value
    made, fga = pair(stats.get(PAIRS["fg"]), "field goals")
    threes, tpa = pair(stats.get(PAIRS["three"]), "three pointers")
    ftm, fta = pair(stats.get(PAIRS["ft"]), "free throws")
    if threes > made or tpa > fga:
        raise BoxscoreGap("three-point totals exceed field goals")
    orb = number(stats.get("offensiveRebounds"), "offensive rebounds")
    drb_raw = stats.get("defensiveRebounds")
    if drb_raw in (None, ""):
        total_reb = number(stats.get("totalRebounds"), "total rebounds")
        drb = total_reb - orb
        if drb < 0:
            raise BoxscoreGap("offensive rebounds exceed total rebounds")
    else:
        drb = number(drb_raw, "defensive rebounds")
    turnovers = number(stats.get("totalTurnovers") if stats.get("totalTurnovers") not in (None, "") else stats.get("turnovers"), "turnovers")
    if fga <= 0 or fta > 110 or fga > 160 or turnovers > 60:
        raise BoxscoreGap("implausible shot/turnover totals")
    possessions = fga - orb + turnovers + 0.475 * fta
    if not 35 <= possessions <= 145:
        raise BoxscoreGap("implausible possession estimate")
    return {
        "fgm":made, "fga":fga, "three_m":threes, "three_a":tpa,
        "ftm":ftm, "fta":fta, "orb":orb, "drb":drb,
        "turnovers":turnovers, "poss_est":round(possessions,3)
    }


def parse_summary(payload, game, observed, raw_sha):
    if str(payload.get("header", {}).get("id", "")) != str(game["game_id"]):
        raise BoxscoreGap("game ID mismatch")
    header_comp = (payload.get("header", {}).get("competitions") or [])
    if not header_comp:
        raise BoxscoreGap("missing independent final-score evidence")
    opponents = {str(t.get("id")):t for t in header_comp[0].get("competitors") or []}
    expected = {str(game["home_id"]), str(game["away_id"])}
    if set(opponents) != expected:
        raise BoxscoreGap("summary opponent ID mismatch")
    for side in ("home", "away"):
        tid = str(game[side + "_id"])
        if opponents[tid].get("score") in (None, ""):
            raise BoxscoreGap("summary score missing")
        if number(opponents[tid]["score"], side + " summary score") != number(game[side + "_score"], side + " scoreboard score"):
            raise BoxscoreGap("source score conflict")
    box_teams = (payload.get("boxscore") or {}).get("teams") or []
    lookup = {str(e.get("team", {}).get("id")):e for e in box_teams if isinstance(e,dict)}
    if len(box_teams) != 2 or set(lookup) != expected:
        raise BoxscoreGap("two-sided team boxscore missing")
    home = decode_side(lookup[str(game["home_id"])])
    away = decode_side(lookup[str(game["away_id"])])
    possessions = (home["poss_est"] + away["poss_est"])/2
    if abs(home["poss_est"] - away["poss_est"]) > 15:
        raise BoxscoreGap("inconsistent two-sided possession estimates")
    for side, other, prefix in ((home,away,"home"), (away,home,"away")):
        points = number(game[prefix + "_score"], prefix + " points")
        opponent_drb = other["drb"]
        side.update({
            "points":int(points),
            "efg_pct":round((side["fgm"]+0.5*side["three_m"])/side["fga"],4),
            "tov_pct":round(side["turnovers"]/max(side["poss_est"],1),4),
            "orb_pct":round(side["orb"]/(side["orb"]+opponent_drb),4) if side["orb"]+opponent_drb>0 else None,
            "ft_rate":round(side["fta"]/side["fga"],4),
            "three_rate":round(side["three_a"]/side["fga"],4),
            "off_rtg":round(100*points/possessions,2),
            "pace_est":round(possessions,2)
        })
    return {
        "schema":1, "source":"ESPN game summary team boxscore",
        "source_captured_utc":observed, "raw_sha256":raw_sha,
        "game_id":str(game["game_id"]), "tip_utc":game["tip_utc"],
        "season":int(game["season"]), "home_id":str(game["home_id"]),
        "away_id":str(game["away_id"]), "home":home, "away":away,
        "status":"RESEARCH_OBSERVED_POSTGAME"
    }


def load_accepted(path):
    accepted = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj = json.loads(line)
                gid = str(obj["game_id"])
                if gid in accepted:
                    raise ValueError("duplicate immutable boxscore ID " + gid)
                accepted[gid] = obj
    return accepted


def choose_games(games, seen, now, max_games=80, previous_share=None,
                 d1_ids=None, prior_share=None):
    """Backward-compatible public entrypoint to quota-based source selection."""
    from cbb_data_quality import select_games
    if previous_share is None:
        previous_share = int(round(max_games * 0.55))
    if prior_share is None:
        prior_share = int(round(max_games * 0.30))
    return select_games(games, seen, now, max_games=max_games,
                        d1_ids=d1_ids, previous_quota=previous_share,
                        prior_quota=prior_share)


def fetch_summary(game_id, opener=None):
    """Retry rate-limits / transient failures with bounded backoff; fail closed."""
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(SUMMARY_API+str(game_id),
          headers={"Accept":"application/json","User-Agent":"cbb-public-research/0.5"})
    for attempt in range(3):
        try:
            with opener(req, timeout=25) as response:
                raw = response.read()
            return json.loads(raw), raw
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt == 2:
                raise
            retry = exc.headers.get("Retry-After") if exc.headers else None
            delay = min(30.0,max(2.0*2**attempt,float(retry))) if retry and str(retry).replace('.','',1).isdigit() else 2.0*2**attempt
            time.sleep(delay)
        except (urllib.error.URLError,TimeoutError,OSError,json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(2.0*2**attempt)


def profiles(accepted, year, d1_ids=None):
    d1_ids = set(str(x) for x in (d1_ids or []))
    if not d1_ids:
        return {}  # Never label unverified teams as D-I research profiles.
    teams = defaultdict(list)
    for r in accepted.values():
        if r["season"] != year:
            continue
        # Avoid 'guarantee games' distorting the first team efficiency profiles.
        if d1_ids and (r["home_id"] not in d1_ids or r["away_id"] not in d1_ids):
            continue
        teams[r["home_id"]].append(r["home"])
        teams[r["away_id"]].append(r["away"])
    out = {}
    features = ["pace_est","off_rtg","efg_pct","tov_pct","orb_pct","ft_rate","three_rate"]
    for tid, games in teams.items():
        out[tid] = {"team_id":tid,"games":len(games),"min_games_required_for_analysis":5,
                    "status":"RESEARCH_SAMPLE" if len(games)>=5 else "INSUFFICIENT"}
        for f in features:
            values=[g[f] for g in games if g[f] is not None]
            out[tid][f]=round(sum(values)/len(values),4) if values else None
    return out


def run(root="data", max_games=80, delay=1.2, opener=None, now=None):
    now=now or utcnow()
    root=Path(root)
    advanced=root/"advanced"
    advanced.mkdir(parents=True,exist_ok=True)
    ledger=advanced/"boxscore_team_games.jsonl"
    accepted=load_accepted(ledger)
    games=read_csv(root/"games.csv")
    # Optional directory for verified D-I-only historical backfill. A missing
    # directory is visible in the audit; it is not proof of D-I membership.
    try:
        from team_directory import load_directory
        d1=load_directory(root/"team_directory.json")
    except (ValueError,OSError):
        d1={}
    candidates=choose_games(games,accepted,now,max_games,
                            d1_ids=d1.keys())
    good, failures=[],[]
    for i,g in enumerate(candidates):
        if i and delay:
            time.sleep(delay)
        gid=str(g["game_id"])
        try:
            payload, raw=fetch_summary(gid,opener=opener)
            captured=iso(utcnow())
            digest=hashlib.sha256(raw).hexdigest()
            record=parse_summary(payload,g,captured,digest)
        except (OSError,ValueError,KeyError,TypeError,OverflowError) as exc:
            failures.append({"game_id":gid,"error":type(exc).__name__+": "+str(exc)[:160]})
            continue
        # Preserve a minimal original-statistics evidence excerpt, not massive player PBP.
        preview={"header":{"id":payload["header"]["id"],
                  "competitions":payload["header"]["competitions"]},
                 "boxscore":{"teams":payload["boxscore"]["teams"]},
                 "captured_utc":captured,"raw_sha256":digest}
        snapshot=advanced/"raw_team_boxscores"/str(g["season"])/(gid+".json.gz")
        snapshot.parent.mkdir(parents=True,exist_ok=True)
        with gzip.open(snapshot,"wt",encoding="utf-8") as handle:
            json.dump(preview,handle,separators=(",",":"))
        # Close each appended record before moving to the next request.
        # Workflow commits each bounded chunk so interruptions retain prior chunks.
        with ledger.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps(record,sort_keys=True)+"\n")
        good.append(record)
        accepted[gid]=record
    year=now.year+int(now.month>=7)
    prof=profiles(accepted,year,d1.keys())
    from cbb_data_quality import audit
    source_audit=audit(games,accepted,now,d1.keys())
    (advanced/"coverage_audit.json").write_text(
        json.dumps(source_audit,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    report={
        "status":"RESEARCH_ONLY" if good else "NO_NEW_ACCEPTED_BOX_SCORES",
        "model_unchanged":True,"source":"ESPN public game summary",
        "audit_status":source_audit["status"],"audit_by_season":source_audit["by_season"],
        "quarantined_archive_rows":source_audit["quarantined_rows"],
        "as_of_utc":iso(now),"attempted":len(candidates),
        "accepted_this_run":len(good),"rejected_this_run":len(failures),
        "accepted_total":len(accepted),"profile_teams":len(prof),
        "profile_teams_5plus":sum(x["games"]>=5 for x in prof.values()),
        "rejections":failures[:50],
        "warning":"Retrospective observation is not historical availability. Features are not used in active model picks."
    }
    (advanced/"boxscore_coverage.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    (advanced/("team_profiles_"+str(year)+".json")).write_text(json.dumps(prof,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="data")
    ap.add_argument("--max-games",type=int,default=80)
    ap.add_argument("--delay",type=float,default=1.2)
    args=ap.parse_args()
    if not 1<=args.max_games<=120 or not 1.2<=args.delay<=10:
        ap.error("Free-source guardrails: 1..120 games per chunk, >=1.2s between calls.")
    result=run(args.root,args.max_games,args.delay)
    print(json.dumps(result,indent=2))
    if result["attempted"]>=10 and result["accepted_this_run"]==0:
        raise SystemExit("No valid boxscores obtained: investigate source and coverage.")


if __name__=="__main__":
    main()
