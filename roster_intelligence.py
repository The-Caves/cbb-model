"""Provenance-first NCAA MBB player minutes, roster and transfer continuity.

Research-only. Player participation is parsed from ESPN game summaries;
current roster snapshots come from ESPN's team endpoint. No projected starters,
roster-era assumptions, or backdated capture timestamps are manufactured.
"""
import argparse
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
from team_directory import load_directory

ROSTER_API = ("https://site.api.espn.com/apis/site/v2/sports/"
              "basketball/mens-college-basketball/teams/{}?enable=roster")
ROSTER_MAX_AGE_DAYS = 30
MIN_PRIOR_GAMES = 5


class PlayerEvidenceGap(ValueError):
    pass


def _number(v, field, hi):
    try:
        f = float(v)
    except (ValueError, TypeError) as exc:
        raise PlayerEvidenceGap("missing/invalid " + field) from exc
    if not math.isfinite(f) or not 0 <= f <= hi:
        raise PlayerEvidenceGap("implausible " + field)
    return f


def _minutes(v):
    if isinstance(v, str) and re.fullmatch(r"\d+:\d{1,2}", v):
        minutes, seconds = (int(x) for x in v.split(":"))
        if seconds >= 60:
            raise PlayerEvidenceGap("minutes seconds out of range")
        return _number(minutes + seconds / 60, "minutes", 65)
    return _number(v, "minutes", 65)


def player_boxscore(payload, game, captured, raw_sha):
    """Extract only complete, score-reconciled two-team player evidence.

    Caller already verified this exact ESPN game summary with parse_summary.
    Missing per-player statistics are *not* treated as zeros. Failing this
    optional parser never blocks verified team-boxscore ingestion.
    """
    if str(payload.get("header", {}).get("id")) != str(game["game_id"]):
        raise PlayerEvidenceGap("game identity mismatch")
    wanted = {str(game["home_id"]), str(game["away_id"])}
    blocks = (payload.get("boxscore") or {}).get("players")
    if not isinstance(blocks, list) or len(blocks) != 2:
        raise PlayerEvidenceGap("two-sided player boxscore unavailable")
    by_team = {}
    all_player_ids = set()
    for team in blocks:
        tid = str((team.get("team") or {}).get("id", ""))
        if tid not in wanted or tid in by_team:
            raise PlayerEvidenceGap("player team identities mismatch")
        athletes = []
        seen = set()
        for stat_group in team.get("statistics") or []:
            labels = stat_group.get("names") or stat_group.get("labels") or []
            if not isinstance(labels, list):
                continue
            labels = [str(v).upper().strip() for v in labels]
            columns = {}
            for needed in ("MIN", "PTS", "REB", "AST"):
                if needed not in labels:
                    break
                columns[needed] = labels.index(needed)
            if len(columns) != 4:
                continue
            for row in stat_group.get("athletes") or []:
                athlete = row.get("athlete") or {}
                player_id = str(athlete.get("id", "")).strip()
                vals = row.get("stats")
                if not player_id or not isinstance(vals, list):
                    continue
                if player_id in seen or player_id in all_player_ids:
                    raise PlayerEvidenceGap("duplicate or cross-team player ID")
                try:
                    minute = _minutes(vals[columns["MIN"]])
                    pts = _number(vals[columns["PTS"]], "points", 100)
                    reb = _number(vals[columns["REB"]], "rebounds", 50)
                    ast = _number(vals[columns["AST"]], "assists", 40)
                except (IndexError, PlayerEvidenceGap):
                    continue  # DNP or absent row is not a zero stat line.
                seen.add(player_id)
                all_player_ids.add(player_id)
                athletes.append({"player_id": player_id, "team_id": tid,
                                 "minutes": round(minute, 3),
                                 "points": pts, "rebounds": reb, "assists": ast})
        if len(athletes) < 5:
            raise PlayerEvidenceGap("too few valid participants")
        minutes = sum(a["minutes"] for a in athletes)
        points = sum(a["points"] for a in athletes)
        score = _number(game["home_score"] if tid == str(game["home_id"])
                        else game["away_score"], "official score", 200)
        if not 190 <= minutes <= 300 or abs(points - score) > 0.001:
            raise PlayerEvidenceGap("player minutes or total points not reconciled")
        by_team[tid] = athletes
    if set(by_team) != wanted:
        raise PlayerEvidenceGap("player identities incomplete")
    if dt(captured) <= dt(game["tip_utc"]):
        raise PlayerEvidenceGap("player boxscore before tip")
    if not re.fullmatch("[0-9a-f]{64}", raw_sha or ""):
        raise PlayerEvidenceGap("missing full source hash")
    return {"schema": 1, "source": "ESPN game summary player boxscore",
            "source_captured_utc": captured, "raw_sha256": raw_sha,
            "game_id": str(game["game_id"]), "tip_utc": game["tip_utc"],
            "season": int(game["season"]), "home_id": str(game["home_id"]),
            "away_id": str(game["away_id"]),
            "players": sorted(by_team[str(game["home_id"])] +
                              by_team[str(game["away_id"])],
                              key=lambda x: (x["team_id"], x["player_id"])),
            "status": "SOURCE_OBSERVED_AFTER_FINAL"}


def roster_snapshot(payload, team_id, captured, digest):
    """Current roster *at actual capture*, never assert historical membership."""
    obj = payload.get("team") or {}
    if str(obj.get("id")) != str(team_id):
        raise PlayerEvidenceGap("roster team ID mismatch")
    roster = obj.get("athletes")
    if not isinstance(roster, list):
        raise PlayerEvidenceGap("missing current roster athletes")
    ids = set()
    for athlete in roster:
        person = athlete.get("athlete", athlete) if isinstance(athlete, dict) else {}
        ident = str(person.get("id", "")).strip()
        if not ident or not ident.isdigit() or ident in ids:
            raise PlayerEvidenceGap("invalid or duplicate roster player ID")
        ids.add(ident)
    if not 8 <= len(ids) <= 30:
        raise PlayerEvidenceGap("incomplete/implausible current roster")
    if not re.fullmatch("[0-9a-f]{64}", digest or ""):
        raise PlayerEvidenceGap("invalid source hash")
    return {"schema": 1, "source": "ESPN team current roster",
            "source_captured_utc": captured, "raw_sha256": digest,
            "season": dt(captured).year + int(dt(captured).month >= 7),
            "team_id": str(team_id), "player_ids": sorted(ids),
            "historical_membership_authenticated": False,
            "status": "CURRENT_ROSTER_OBSERVED_ONLY"}


def read_jsonl(path, unique_key=None):
    rows = []
    path = Path(path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if unique_key:
        seen = set()
        for row in rows:
            key = unique_key(row)
            if key in seen:
                raise PlayerEvidenceGap("duplicate immutable source evidence")
            seen.add(key)
    return rows


def continuity(player_games, snapshots, cutoff, season, verified):
    """Use verified prior production and roster observed *by* cutoff.

    A point-in-time roster is evidence of present membership only. This
    produces a *sample-backed* prior-usage share, not projected lineup minutes.
    A nonrepresentative sample is flagged, never labeled complete.
    """
    verified = {str(x) for x in verified}
    prior_year = season - 1
    latest = {}
    for row in snapshots:
        team = str(row.get("team_id"))
        if team not in verified or int(row.get("season", -1)) != season:
            continue
        captured = dt(row["source_captured_utc"])
        if captured > cutoff or cutoff - captured > timedelta(days=ROSTER_MAX_AGE_DAYS):
            continue
        if not 8 <= len(set(row.get("player_ids") or [])) <= 30:
            continue
        if team not in latest or latest[team]["source_captured_utc"] < row["source_captured_utc"]:
            latest[team] = row
    usage = defaultdict(lambda: defaultdict(lambda: {"minutes": 0.0, "points": 0.0}))
    team_games = defaultdict(set)
    prior_player_team = defaultdict(dict)
    for game in player_games:
        try:
            if int(game["season"]) != prior_year:
                continue
            # Real as-of features require actual archived capture by cutoff.
            if dt(game["source_captured_utc"]) > cutoff:
                continue
            if str(game["home_id"]) not in verified or str(game["away_id"]) not in verified:
                continue
            for person in game["players"]:
                team = str(person["team_id"])
                ident = str(person["player_id"])
                usage[team][ident]["minutes"] += float(person["minutes"])
                usage[team][ident]["points"] += float(person["points"])
                team_games[team].add(str(game["game_id"]))
                prior_player_team[ident][team] = prior_player_team[ident].get(team, 0.0) + float(person["minutes"])
        except (ValueError, TypeError, KeyError):
            continue
    profiles = {}
    for team, roster in latest.items():
        games = len(team_games[team])
        total_min = sum(v["minutes"] for v in usage[team].values())
        total_pts = sum(v["points"] for v in usage[team].values())
        ids = set(roster["player_ids"])
        if games < MIN_PRIOR_GAMES or total_min < 190 * MIN_PRIOR_GAMES:
            profiles[team] = {"team_id": team, "status": "UNKNOWN_INSUFFICIENT_PRIOR_PLAYER_GAMES",
                              "prior_games_verified": games,
                              "roster_player_count": len(ids), "retained_minutes_share": None}
            continue
        returners = ids & set(usage[team])
        transfers = {player for player in ids if player not in usage[team]
                     and any(old != team for old in prior_player_team.get(player, {}))}
        retained_minutes = sum(usage[team][pid]["minutes"] for pid in returners)
        retained_points = sum(usage[team][pid]["points"] for pid in returners)
        transfer_prior_minutes = sum(
            minv for pid in transfers for old, minv in prior_player_team[pid].items()
            if old != team)
        profiles[team] = {
            "team_id": team,
            "status": "RESEARCH_SAMPLE_NOT_FULL_SEASON",
            "roster_source_captured_utc": roster["source_captured_utc"],
            "roster_player_count": len(ids),
            "prior_games_verified": games,
            "prior_sample_player_minutes": round(total_min, 2),
            "returning_player_count": len(returners),
            "transfers_with_prior_verified_minutes": len(transfers),
            "transfers_prior_sample_minutes": round(transfer_prior_minutes, 2),
            "roster_members_without_prior_observed_games": len(
                ids - returners - transfers),
            "retained_minutes_share": round(retained_minutes / total_min, 5),
            "retained_points_share": round(retained_points / total_pts, 5)
                if total_pts > 0 else None,
            "caveat": ("Observed prior games are a partial historical sample; "
                       "transfer usage does not imply minutes at the new school."),
        }
    return profiles


def _get(url, opener=None):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "cbb-roster-research/0.1"})
    for attempt in range(3):
        try:
            with opener(request, timeout=25) as response:
                raw = response.read()
            return json.loads(raw), hashlib.sha256(raw).hexdigest()
        except urllib.error.HTTPError as err:
            if err.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (TimeoutError, OSError, ValueError):
            if attempt == 2:
                raise
        time.sleep(2.0 ** attempt)


def upcoming_teams(games, now, verified):
    """Nearest valid future D-I matchups first; never use a completed score."""
    valid = {str(x) for x in verified}
    matched = []
    for g in games:
        try:
            if (str(g["home_id"]) not in valid or
                    str(g["away_id"]) not in valid or
                    str(g.get("score_conflict", "0")) in ("1", "true", "True") or
                    str(g.get("identity_conflict", "0")) in ("1", "true", "True")):
                continue
            tip = dt(g["tip_utc"])
            if now < tip <= now + timedelta(hours=72):
                matched.append(g)
        except (ValueError, TypeError, KeyError):
            continue
    ordered = []
    seen = set()
    for g in sorted(matched, key=lambda x: (x["tip_utc"], x["game_id"])):
        for key in ("home_id", "away_id"):
            team = str(g[key])
            if team not in seen:
                ordered.append(team)
                seen.add(team)
    return ordered


def plan_player_backfill(candidates, existing_players, focus, year, limit):
    """Prioritize recent prior-season games for teams on the upcoming slate.

    Only pre-verified source candidates are passed in. Concentrate each
    bounded batch on reaching five player game samples for actual matchups.
    Unused capacity falls back to the chronological archive backlog.
    """
    by_id = {str(x["game_id"]): x for x in candidates}
    counts = defaultdict(set)
    for rec in existing_players:
        try:
            if int(rec["season"]) != year - 1:
                continue
            for key in ("home_id", "away_id"):
                counts[str(rec[key])].add(str(rec["game_id"]))
        except (TypeError, KeyError, ValueError):
            continue
    prior = sorted((g for g in candidates if int(g["season"]) == year - 1),
                   key=lambda g: g["tip_utc"], reverse=True)
    selected = []
    seen = set()
    def add(g):
        gid = str(g["game_id"])
        if len(selected) >= limit or gid in seen:
            return False
        seen.add(gid)
        selected.append(g)
        if int(g["season"]) == year - 1:
            for key in ("home_id", "away_id"):
                counts[str(g[key])].add(gid)
        return True
    for team in focus:
        for g in prior:
            if len(selected) >= limit or len(counts[str(team)]) >= MIN_PRIOR_GAMES:
                break
            if str(g["home_id"]) == str(team) or str(g["away_id"]) == str(team):
                add(g)
    for g in candidates:
        add(g)
    return selected


def collect_current_rosters(root="data", max_teams=12, delay=1.2,
                            opener=None, now=None):
    """Quota-limited current membership intake, never fetch historical names."""
    now = now or utcnow()
    root = Path(root)
    directory = load_directory(root / "team_directory.json")
    if not directory:
        return {"status": "PASS_NO_DIRECTORY", "accepted": 0}
    dest = root / "advanced" / "roster_snapshots.jsonl"
    existing = read_jsonl(dest)
    season = now.year + int(now.month >= 7)
    # Once every team gets a recent roster, refresh oldest first.
    latest = {}
    for row in existing:
        if row.get("season") == season:
            tid = str(row.get("team_id"))
            latest[tid] = max(latest.get(tid, ""), row["source_captured_utc"])
    focus = upcoming_teams(read_csv(root / "games.csv"), now, directory)
    order = {str(team): i for i, team in enumerate(focus)}
    teams = sorted(directory, key=lambda tid: (
        0 if str(tid) in order else 1,
        latest.get(str(tid), ""), order.get(str(tid), 999999),
        str(tid)))[:max_teams]
    accepted, rejected = [], []
    for i, tid in enumerate(teams):
        if i and delay:
            time.sleep(delay)
        try:
            payload, digest = _get(ROSTER_API.format(tid), opener=opener)
            observed = iso(utcnow())
            record = roster_snapshot(payload, tid, observed, digest)
            accepted.append(record)
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as err:
            rejected.append({"team_id": tid, "reason": type(err).__name__})
    if accepted:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as stream:
            for row in accepted:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
    return {"status": "CURRENT_ROSTER_RESEARCH_ONLY", "attempted": len(teams),
            "accepted": len(accepted), "rejected": rejected[:15],
            "source_historical_availability_verified": False}


def collect_player_history(root="data", max_games=48, delay=1.2,
                           opener=None, now=None):
    """Source-verified player history from previously accepted team games.

    Never backdate publication of retrospective ESPN game summaries. Source
    identity, tip, final score, and stable IDs must still match the scoreboard
    and independently archived team boxscore before a player record is saved.
    """
    from boxscore_features import fetch_summary, parse_summary, load_accepted
    from cbb import eligible
    now = now or utcnow()
    root = Path(root)
    verified = set(load_directory(root / "team_directory.json"))
    accepted = load_accepted(root / "advanced" / "boxscore_team_games.jsonl")
    target = root / "advanced" / "player_boxscore_games.jsonl"
    already = {str(row["game_id"]) for row in read_jsonl(
        target, unique_key=lambda row: str(row["game_id"]))}
    year = now.year + int(now.month >= 7)
    candidates = []
    for g in read_csv(root / "games.csv"):
        gid = str(g["game_id"])
        if (gid not in accepted or gid in already or
                int(g["season"]) not in (year, year-1, year-2) or
                str(g["home_id"]) not in verified or
                str(g["away_id"]) not in verified or
                not eligible(g, now, retrospective=False)):
            continue
        src = accepted[gid]
        if (str(src["home_id"]), str(src["away_id"]), str(src["tip_utc"])) != (
                str(g["home_id"]), str(g["away_id"]), str(g["tip_utc"])):
            continue
        candidates.append(g)
    # Prior season first, latest games first, to spread useful returning
    # production across teams without extrapolating unobserved seasons.
    priority = {year-1: 0, year: 1, year-2: 2}
    candidates.sort(key=lambda g: (
        priority[int(g["season"])], -dt(g["tip_utc"]).timestamp(), g["game_id"]))
    focus = upcoming_teams(read_csv(root / "games.csv"), now, verified)
    player_history = read_jsonl(target,
                       unique_key=lambda row: str(row["game_id"]))
    selected = plan_player_backfill(candidates, player_history,
                                    focus, year, max_games)
    good, errors = 0, []
    for i, g in enumerate(selected):
        if i and delay:
            time.sleep(delay)
        try:
            payload, raw_hash = fetch_summary(str(g["game_id"]), opener=opener)
            # Revalidate the current response as a real final for the exact
            # same game as the archived accepted team-stat record.
            observed = iso(utcnow())
            digest = hashlib.sha256(raw_hash).hexdigest()
            team = parse_summary(payload, g, observed, digest)
            previous = accepted[str(g["game_id"])]
            if (team["home"]["points"] != previous["home"]["points"] or
                    team["away"]["points"] != previous["away"]["points"]):
                raise PlayerEvidenceGap("source team score changed")
            player = player_boxscore(payload, g, observed, digest)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as out:
                out.write(json.dumps(player, sort_keys=True) + "\n")
            good += 1
        except (OSError, KeyError, ValueError, TypeError, OverflowError) as err:
            errors.append({"game_id": str(g["game_id"]),
                           "reason": type(err).__name__ + ": " + str(err)[:80]})
    return {"status": "RETROSPECTIVE_PLAYER_RESEARCH_ONLY",
            "attempted": len(selected), "accepted": good,
            "rejections": errors[:20],
            "point_in_time_historical_availability_verified": False}


def run(root="data", now=None, collect=True, max_teams=12,
        player_backfill=0):
    now = now or utcnow()
    root = Path(root)
    collected = (collect_current_rosters(root, max_teams=max_teams, now=now)
                 if collect else {"status": "NO_NETWORK_COLLECTION"})
    historical = (collect_player_history(root, max_games=player_backfill,
                                         now=now)
                  if player_backfill else {"status": "NO_HISTORICAL_BACKFILL"})
    directory = load_directory(root / "team_directory.json")
    player_path = root / "advanced" / "player_boxscore_games.jsonl"
    roster_path = root / "advanced" / "roster_snapshots.jsonl"
    player_games = read_jsonl(player_path,
                              unique_key=lambda r: str(r["game_id"]))
    rosters = read_jsonl(roster_path)
    # The network collector preserves real source capture times; the run
    # started BEFORE those observations existed. Evaluate after collection,
    # never at the earlier start time (which would hide newly captured rosters).
    as_of = max(now, utcnow()) if collect or player_backfill else now
    year = as_of.year + int(as_of.month >= 7)
    prof = continuity(player_games, rosters, as_of, year, directory)
    report = {"status": "RESEARCH_ONLY", "as_of_utc": iso(as_of),
              "live_model_unchanged": True,
              "source": "ESPN current team roster and game-summary player statistics",
              "player_games_total": len(player_games),
              "roster_snapshots_total": len(rosters),
              "roster_teams_recent": len(prof),
              "teams_with_sample_backed_continuity": sum(
                  v["status"] == "RESEARCH_SAMPLE_NOT_FULL_SEASON"
                  for v in prof.values()),
              "collector": collected, "historical_backfill": historical,
              "historical_source_capture_retroactive": True,
              "warning": ("No last-season player data may be treated as available "
                          "at a past tip before actual acquisition. Roster membership "
                          "is a captured snapshot, not a verified starting lineup."),
              "teams": prof}
    target = root / "advanced" / f"roster_continuity_{year}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return {"status": report["status"], "collector": collected,
            "historical_backfill": historical,
            "source_player_games": len(player_games),
            "coverage_teams": report["teams_with_sample_backed_continuity"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data")
    ap.add_argument("--no-collect", action="store_true")
    ap.add_argument("--max-teams", type=int, default=12)
    ap.add_argument("--player-backfill", type=int, default=0)
    args = ap.parse_args()
    if not 1 <= args.max_teams <= 24:
        ap.error("limit requests to 1..24 teams")
    if not 0 <= args.player_backfill <= 48:
        ap.error("limit research re-fetches to 0..48 games")
    print(json.dumps(run(args.root, collect=not args.no_collect,
                         max_teams=args.max_teams,
                         player_backfill=args.player_backfill), indent=2))
