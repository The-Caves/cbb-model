"""Research feature regression guards: correct source, games, as-of cutoffs."""
from datetime import datetime, timedelta, timezone

from team_intelligence import adjusted_factors, build, matched_boxscores

UTC = timezone.utc
BASE = datetime(2026, 11, 1, 18, tzinfo=UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def game(i, day, home="1", away="2", hs=95, ats=55,
         completed=True, neutral=0, season=2027):
    start = BASE + timedelta(days=day)
    return {
        "game_id": str(i), "tip_utc": iso(start), "season": season,
        "home_id": home, "away_id": away, "home": home, "away": away,
        "neutral": neutral, "completed": int(completed),
        "home_score": str(hs) if completed else "",
        "away_score": str(ats) if completed else "",
        "score_conflict": "0", "identity_conflict": "0",
        "first_final_observed_utc": iso(start + timedelta(hours=5)) if completed else "",
    }


def record(g, capture=None, **override):
    stats = {
        "off_rtg": 120.0, "efg_pct": .54, "tov_pct": .15,
        "orb_pct": .31, "ft_rate": .27, "three_rate": .37,
    }
    return {
        "game_id": g["game_id"], "tip_utc": g["tip_utc"],
        "season": g["season"], "home_id": g["home_id"],
        "away_id": g["away_id"],
        "source_captured_utc": capture or iso(
            datetime.fromisoformat(g["tip_utc"].replace("Z", "+00:00"))
            + timedelta(hours=6)),
        "home": {**stats, "points": float(g["home_score"])},
        "away": {**stats, "points": float(g["away_score"])},
        **override,
    }


def test_pretip_cutoff_and_source_capture_are_not_bypassed():
    first = game("one", 0)
    second = game("two", 2, home="2", away="3", hs=70, ats=76)
    future = game("future", 7, home="1", away="3")
    other_division = game("other", 1, home="1", away="999")
    cutoff = BASE + timedelta(days=5)
    observed_later = record(first, capture=iso(cutoff + timedelta(days=1)))
    evidence = {"one": observed_later, "two": record(second),
                "future": record(future), "other": record(other_division)}
    profile = build([first, second, future, other_division], evidence,
                    {"1", "2", "3"}, cutoff)
    assert profile["status"] == "RESEARCH_ONLY_NOT_ACTIVE"
    assert profile["current_d1_finals_used"] == 2
    assert profile["matched_boxscores_used"] == 1
    assert profile["source_capture_authenticated_at_cutoff"]
    assert profile["teams"]["1"]["current_d1_games"] == 1
    assert profile["teams"]["2"]["current_d1_games"] == 2
    assert profile["teams"]["3"]["current_d1_games"] == 1
    assert profile["teams"]["1"]["boxscore_games_current"] == 0
    assert profile["teams"]["2"]["boxscore_games_current"] == 1
    assert profile["teams"]["1"]["schedule_strength_avg_opponent_points"] is not None
    assert profile["teams"]["2"]["strength_of_record_wins_above_expected"] is not None
    assert profile["teams"]["2"]["current_d1_wins"] == 0
    assert profile["teams"]["3"]["current_d1_wins"] == 1
    assert profile["teams"]["1"]["data_status"] == "LOW_CURRENT_SAMPLE"


def test_missing_directory_fails_closed():
    cutoff = BASE + timedelta(days=5)
    profile = build([game("one", 0)], {}, [], cutoff)
    assert profile["status"] == "BLOCKED_NO_VERIFIED_D1_DIRECTORY"
    assert profile["teams"] == {}


def test_boxscore_join_rejects_changed_identity_score_and_future_capture():
    g = game("1", 0)
    cutoff = BASE + timedelta(days=3)
    good = record(g)
    args = ([g], {"1": good}, cutoff, 2027, {"1", "2"}, False)
    assert len(matched_boxscores(*args)) == 1
    assert not matched_boxscores([g], {"1": record(g, home_id="999")},
                                 cutoff, 2027, {"1", "2"}, False)
    assert not matched_boxscores([g], {"1": record(g, home={
        **good["home"], "points": 500.0})},
                                 cutoff, 2027, {"1", "2"}, False)
    assert not matched_boxscores([g], {"1": record(g, capture=iso(
        cutoff + timedelta(hours=1)))}, cutoff, 2027, {"1", "2"}, False)


def test_factor_adjustments_are_shrunk_and_unavailable_values_stay_missing():
    g = game("1", 0)
    rec = record(g)
    rec["home"]["efg_pct"] = None
    profiles = adjusted_factors([rec], 2027)
    assert profiles["1"]["boxscore_games_current"] == 1
    assert profiles["1"]["factor_effective_games"] == 1
    assert "efg_pct_for_adj" not in profiles["1"]
    assert "off_rtg_for_adj" in profiles["1"]
    assert "off_rtg_allowed_adj" in profiles["2"]


def test_retroactive_source_is_only_allowed_in_explicit_research_mode():
    g = game("old", 0)
    cutoff = BASE + timedelta(days=5)
    late = record(g, capture=iso(cutoff + timedelta(days=3)))
    assert not matched_boxscores([g], {"old": late}, cutoff,
                                 2027, {"1", "2"}, False)
    assert len(matched_boxscores([g], {"old": late}, cutoff,
                                 2027, {"1", "2"}, True)) == 1
    report = build([g], {"old": late}, {"1", "2"}, cutoff,
                   retrospective=True)
    assert report["status"] == "RETROSPECTIVE_RESEARCH_ONLY"
    assert not report["source_capture_authenticated_at_cutoff"]
