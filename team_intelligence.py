"""As-of D-I strength of schedule, strength-of-record proxy and Four Factors.

Research-only features. Existing pre-tip forecasts, live model and historical
archives are not rewritten. Historical source availability is NOT authenticated
when running with retrospective=True.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from scipy.stats import norm

from cbb import dt, eligible, fit as score_fit, iso, read_csv, season as season_of, utcnow
from boxscore_features import load_accepted
from team_directory import load_directory

FACTORS = ("off_rtg", "efg_pct", "tov_pct", "orb_pct", "ft_rate", "three_rate")
RANGES = {"off_rtg": (55.0, 180.0), "efg_pct": (0.0, 1.0),
          "tov_pct": (0.0, 1.0), "orb_pct": (0.0, 1.0),
          "ft_rate": (0.0, 2.0), "three_rate": (0.0, 1.0)}


def eligible_d1(games, cutoff, year, d1, retrospective):
    """No future or non-D-I score may influence an as-of profile."""
    return [g for g in games if int(g["season"]) == year
            and str(g["home_id"]) in d1 and str(g["away_id"]) in d1
            and eligible(g, cutoff, retrospective)]


def matched_boxscores(games, accepted, cutoff, year, d1, retrospective):
    """Only source-verified complete boxscores with unambiguous game identities."""
    allowed = {str(g["game_id"]): g for g in games
               if int(g["season"]) in (year, year - 1)
               and str(g["home_id"]) in d1 and str(g["away_id"]) in d1
               and eligible(g, cutoff, retrospective)}
    matched = []
    for gid, rec in accepted.items():
        g = allowed.get(str(gid))
        if g is None:
            continue
        try:
            if (str(rec["game_id"]) != str(gid)
                    or int(rec["season"]) != int(g["season"])
                    or str(rec["home_id"]) != str(g["home_id"])
                    or str(rec["away_id"]) != str(g["away_id"])
                    or rec["tip_utc"] != g["tip_utc"]
                    or dt(rec["source_captured_utc"]) < dt(g["tip_utc"])
                    or (not retrospective and dt(rec["source_captured_utc"]) > cutoff)
                    or any(float(rec[s]["points"]) != float(g[s + "_score"])
                           for s in ("home", "away"))):
                continue
            for side in ("home", "away"):
                for factor in FACTORS:
                    value = rec[side].get(factor)
                    if value is not None:
                        val = float(value)
                        lo, hi = RANGES[factor]
                        if not math.isfinite(val) or not lo <= val <= hi:
                            raise ValueError("invalid factor")
            matched.append(rec)
        except (KeyError, ValueError, OverflowError, TypeError):
            continue
    return matched


def adjusted_factors(records, year, previous_game_weight=0.10,
                     shrink_games=5.0, iterations=7):
    """Regularized two-way offense vs opponent-allowed rate estimation.

    Descriptive factors only, not a weighted margin adjustment. The separate
    scoring model remains the active model until independently validated.
    """
    samples = {factor: [] for factor in FACTORS}
    counts = defaultdict(lambda: [0, 0])
    for rec in records:
        h, a = str(rec["home_id"]), str(rec["away_id"])
        previous = int(rec["season"]) != year
        weight = previous_game_weight if previous else 1.0
        for t in (h, a):
            counts[t][1 if previous else 0] += 1
        for own, opp, side in ((h, a, "home"), (a, h, "away")):
            for factor in FACTORS:
                v = rec[side].get(factor)
                if v is not None:
                    samples[factor].append((own, opp, float(v), weight))
    output = defaultdict(dict)
    for factor, obs in samples.items():
        weight_sum = sum(w for _, _, _, w in obs)
        if not weight_sum:
            continue
        mean = sum(v * w for _, _, v, w in obs) / weight_sum
        attack, allowed = {}, {}
        for _ in range(iterations):
            sums, weights = defaultdict(float), defaultdict(float)
            for own, opp, v, w in obs:
                sums[own] += w * (v - mean - allowed.get(opp, 0))
                weights[own] += w
            attack = {t: s / (weights[t] + shrink_games) for t, s in sums.items()}
            sums, weights = defaultdict(float), defaultdict(float)
            for own, opp, v, w in obs:
                sums[opp] += w * (v - mean - attack.get(own, 0))
                weights[opp] += w
            allowed = {t: s / (weights[t] + shrink_games) for t, s in sums.items()}
        # Do not manufacture a factor for a team without that side of the
        # observed game statistic. Missing remains missing, not league mean.
        for t in set(attack) | set(allowed):
            if t in attack:
                output[t][factor + "_for_adj"] = round(mean + attack[t], 5)
            if t in allowed:
                output[t][factor + "_allowed_adj"] = round(mean + allowed[t], 5)
    for t, (current, prior) in counts.items():
        output[t]["boxscore_games_current"] = current
        output[t]["boxscore_games_previous"] = prior
        output[t]["factor_effective_games"] = round(
            current + previous_game_weight * prior, 2)
    return output


def build(games, accepted, verified_ids, cutoff, year=None, retrospective=False,
          score_model=None):
    cutoff = dt(cutoff) if isinstance(cutoff, str) else cutoff
    year = year or season_of(cutoff)
    d1 = {str(v) for v in verified_ids}
    if not d1:
        return {"status": "BLOCKED_NO_VERIFIED_D1_DIRECTORY", "teams": {}}
    current = eligible_d1(games, cutoff, year, d1, retrospective)
    try:
        model = score_model if score_model is not None else score_fit(
            games, cutoff, test_season=year, retrospective=retrospective,
            alpha=1.5, prior_weight=0.30, verified_ids=d1)
    except ValueError:
        return {"status": "BLOCKED_NO_ELIGIBLE_TRAINING", "teams": {}}
    if model["season"] != year or model["cutoff"] != iso(cutoff) or not model["verified_d1_training_only"]:
        raise ValueError("incompatible as-of D-I score model supplied")
    idx, beta = model["idx"], model["beta"]
    n = len(idx)
    # From score fit: a team's neutral margin contribution is its
    # offensive effect PLUS its defensive prevention effect.
    ratings = {team: float(beta[2 + i] + beta[2 + n + i])
               for team, i in idx.items()}
    schedules = defaultdict(list)
    wins = defaultdict(int)
    for g in sorted(current, key=lambda x: (x["tip_utc"], str(x["game_id"]))):
        h, a = str(g["home_id"]), str(g["away_id"])
        venue = 0.0 if str(g.get("neutral", "0")) in ("1", "True", "true") else 3.5
        margin = float(g["home_score"]) - float(g["away_score"])
        for team, opponent, team_margin, team_venue in (
                (h, a, margin, venue), (a, h, -margin, -venue)):
            opponent_strength = ratings.get(opponent, 0.0)
            # Average D-I team vs this opponent at this venue:
            expected_win = float(norm.cdf(
                (-opponent_strength + team_venue) / model["margin_sd"]))
            schedules[team].append(
                (opponent_strength, team_margin, team_venue, expected_win))
            wins[team] += int(team_margin > 0)
    boxscores = matched_boxscores(games, accepted, cutoff, year, d1,
                                  retrospective)
    factors = adjusted_factors(boxscores, year)
    profiles = {}
    for t in sorted(d1):
        rows = schedules[t]
        count = len(rows)
        expected = sum(r[3] for r in rows)
        profiles[t] = {
            "team_id": t,
            "current_d1_games": count,
            "current_d1_wins": wins[t],
            "current_d1_losses": count - wins[t],
            "opponent_adjusted_strength_points": (
                round(ratings[t], 3) if t in ratings else None),
            "schedule_strength_avg_opponent_points": (
                round(sum(x[0] for x in rows) / count, 3) if count else None),
            "strength_of_record_expected_wins": round(expected, 3) if count else None,
            "strength_of_record_wins_above_expected": (
                round(wins[t] - expected, 3) if count else None),
            "last5_opponent_adjusted_margin": (
                round(sum(m + opp - venue for opp, m, venue, _ in rows[-5:])
                      / len(rows[-5:]), 3) if count else None),
            "data_status": "LOW_CURRENT_SAMPLE" if count < 5 else "RESEARCH_ONLY",
        }
        profiles[t].update(factors.get(t, {
            "boxscore_games_current": 0, "boxscore_games_previous": 0,
            "factor_effective_games": 0,
        }))
    return {
        "status": "RETROSPECTIVE_RESEARCH_ONLY" if retrospective
                  else "RESEARCH_ONLY_NOT_ACTIVE",
        "as_of_utc": iso(cutoff), "season": year,
        "d1_directory_teams": len(d1),
        "current_d1_finals_used": len(current),
        "matched_boxscores_used": len(boxscores),
        "current_season_boxscores_used": sum(int(r["season"]) == year
                                             for r in boxscores),
        "source_capture_authenticated_at_cutoff": not retrospective,
        "active_score_model_unchanged": True,
        "sor_definition": ("Wins minus expected wins of average D-I team "
                           "versus identical opponents and venues; proxy"),
        "prior_factor_weight_per_previous_game": 0.10,
        "caveat": ("Partial ESPN sample. Prior team stats may be stale after "
                   "transfers. No conference-tier assumed strengths, "
                   "verified injury/roster context or market edges."),
        "teams": profiles,
    }


def run(root="data", now=None):
    root = Path(root)
    now = now or utcnow()
    try:
        d1 = load_directory(root / "team_directory.json")
    except (OSError, ValueError):
        d1 = {}
    result = build(read_csv(root / "games.csv"),
                   load_accepted(root / "advanced" / "boxscore_team_games.jsonl"),
                   d1, now)
    target = root / "advanced" / f"team_intelligence_{season_of(now)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return {"status": result["status"], "teams": len(result["teams"]),
            "matched_boxscores_used": result.get("matched_boxscores_used"),
            "path": str(target)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="data")
    args = p.parse_args()
    print(json.dumps(run(args.root), indent=2))
