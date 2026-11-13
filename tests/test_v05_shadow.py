"""V0.5 shadow is immutable pre-tip evidence, never live model replacement."""
import json
from datetime import datetime, timedelta, timezone

from cbb import ACTIVE_LIVE_VERSION
from v05_shadow import freeze, grade, model_name, validated_candidate

UTC = timezone.utc


def iso(x):
    return x.isoformat().replace("+00:00", "Z")


def game(i, tip, h="1", a="2", hs=77, ats=68, completed=False):
    return {"game_id": str(i), "tip_utc": iso(tip), "season": 2027,
            "home_id": h, "away_id": a, "home": h, "away": a,
            "neutral": "0", "completed": str(int(completed)),
            "home_score": str(hs) if completed else "",
            "away_score": str(ats) if completed else "",
            "first_final_observed_utc": iso(tip+timedelta(hours=5)) if completed else "",
            "score_conflict": "0", "identity_conflict": "0"}


def test_no_directory_or_games_creates_no_predictions(tmp_path):
    now = datetime(2026, 11, 14, 15, tzinfo=UTC)
    game_future = game("g", now+timedelta(hours=6))
    out = tmp_path/"v05.jsonl"
    assert freeze([game_future], [], out, now)["captured"] == 0
    assert not out.exists()
    assert freeze([game_future], ["1", "3"], out, now)["captured"] == 0
    assert not out.exists()


def test_freezes_all_three_variants_strictly_pretip(tmp_path):
    now = datetime(2026, 11, 14, 15, tzinfo=UTC)
    old = [game(str(i), now-timedelta(days=3-i), completed=True)
           for i in range(3)]
    future = game("next", now+timedelta(hours=6))
    outside = game("outside", now+timedelta(hours=6), h="1", a="999")
    started = game("started", now-timedelta(minutes=1))
    out = tmp_path/"v05.jsonl"
    report = freeze(old+[future, outside, started], ["1", "2"], out, now)
    assert report["captured"] == 3
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 3
    assert {r["game_id"] for r in rows} == {"next"}
    assert all(validated_candidate(r, future, now) for r in rows)
    assert freeze(old+[future, outside, started], ["1", "2"], out, now)["captured"] == 0
    # A game at kickoff never becomes a new research forecast.
    assert freeze(old+[future, outside, started], ["1", "2"], out,
                  now+timedelta(hours=6))["captured"] == 0


def test_tampered_shadow_or_champion_is_not_grading_evidence(tmp_path):
    tip = datetime(2026, 11, 15, 19, tzinfo=UTC)
    now = tip + timedelta(hours=8)
    g = game("g", tip, completed=True)
    champion = tmp_path/"champion.jsonl"
    shadow = tmp_path/"shadow.jsonl"
    base = {
        "model": ACTIVE_LIVE_VERSION, "game_id": "g",
        "tip_utc": iso(tip), "home_id": "1", "away_id": "2",
        "captured_utc": iso(tip-timedelta(hours=5)),
        "pred_home": 74, "pred_away": 69,
        "pred_margin": 5, "pred_total": 143,
        "home_current_games": 2, "away_current_games": 2,
    }
    champion.write_text(json.dumps(base)+"\n")
    invalid = {
        **base, "model": model_name("roster_fade"),
        "training_latest_tip": iso(tip-timedelta(hours=12)),
        "captured_utc": iso(tip+timedelta(minutes=2)),
    }
    shadow.write_text(json.dumps(invalid)+"\n")
    report = grade([g], champion, shadow, ["1", "2"], now)
    assert report["paired_games_each_variant_min"] == 0
    assert report["variants"]["roster_fade"]["v05"]["n"] == 0
    assert report["promotion_gate"] == "INSUFFICIENT_PROSPECTIVE_HOLDOUT"


def test_invalid_tips_training_and_totals_are_rejected():
    tip = datetime(2026, 11, 15, 19, tzinfo=UTC)
    capture = tip-timedelta(hours=3)
    g = game("g", tip)
    prediction = {"model": model_name("lighter_carryover"),
                  "game_id": "g", "home_id": "1", "away_id": "2",
                  "tip_utc": iso(tip), "captured_utc": iso(capture),
                  "pred_home": 80, "pred_away": 70, "pred_margin": 10,
                  "pred_total": 150,
                  "training_latest_tip": iso(capture-timedelta(hours=5))}
    assert validated_candidate(prediction, g, capture)
    assert not validated_candidate({**prediction, "pred_total": 130}, g, capture)
    assert not validated_candidate({**prediction, "training_latest_tip":
                                    iso(capture+timedelta(minutes=2))}, g, capture)
