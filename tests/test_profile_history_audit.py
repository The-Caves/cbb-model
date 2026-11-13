"""Saved team-profile history must represent actual dated Git revisions."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from profile_history_audit import compare, dated_snapshots, build_audit


def profile(asof, strength):
    return {
        "as_of_utc":asof,
        "season":2027,
        "teams":{
            "1":{"current_d1_games":3,
                 "opponent_adjusted_strength_points":strength,
                 "schedule_strength_avg_opponent_points":-4,
                 "last5_opponent_adjusted_margin":strength+2},
            "2":{"current_d1_games":3,
                 "opponent_adjusted_strength_points":-2,
                 "schedule_strength_avg_opponent_points":1,
                 "last5_opponent_adjusted_margin":-1}
        },
    }


def test_compare_saved_profiles_measures_real_changes_and_ranks():
    earlier=profile("2026-11-11T19:00:00Z",-5)
    later=profile("2026-11-13T19:00:00Z",3)
    report=compare(earlier,later,"a"*40)
    assert report["status"]=="SAVED_AS_OF_PROFILE_COMPARISON"
    assert report["team_ids_compared"]==2
    riser=report["teams_rising_most"][0]
    assert riser["team_id"]=="1"
    assert riser["rank_old"]==2 and riser["rank_latest"]==1
    assert riser["strength_change_points"]==8
    assert riser["profile_changes"]["schedule_strength_avg_opponent_points"]==0
    assert report["old_profile_git_commit"]=="a"*40


def test_recent_profiles_are_not_mislabeled_old_history():
    now=profile("2026-11-13T19:00:00Z",0)
    too_recent=profile("2026-11-13T18:00:00Z",1)
    report=compare(too_recent,now,"b"*40)
    assert report["status"]=="INSUFFICIENT_SAVED_PROFILE_HISTORY"


def test_history_audit_uses_real_git_commit_not_fabricated_replay(tmp_path):
    repo=tmp_path/"repo"
    repo.mkdir()
    subprocess.run(["git","init"],cwd=repo,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","ci@example.test"],cwd=repo,check=True)
    subprocess.run(["git","config","user.name","CI Test"],cwd=repo,check=True)
    file=repo/"data"/"advanced"/"team_intelligence_2027.json"
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps(profile("2026-11-11T19:00:00Z",-5)))
    subprocess.run(["git","add","."],cwd=repo,check=True)
    subprocess.run(["git","commit","-m","first saved profiles"],cwd=repo,check=True,
                   capture_output=True)
    file.write_text(json.dumps(profile("2026-11-12T19:00:00Z",-2)))
    subprocess.run(["git","add","."],cwd=repo,check=True)
    subprocess.run(["git","commit","-m","second saved profiles"],cwd=repo,check=True,
                   capture_output=True)
    # The newest working tree version has not yet been committed. The
    # audit must still use the real earlier historical Git revision.
    file.write_text(json.dumps(profile("2026-11-13T19:00:00Z",3)))
    saved=dated_snapshots(repo,"data/advanced/team_intelligence_2027.json")
    assert len(saved)==2
    report=build_audit(root=repo/"data",repo=repo,year=2027)
    assert report["team_ids_compared"]==2
    assert report["old_profile_as_of_utc"]=="2026-11-12T19:00:00Z"
    assert report["teams_rising_most"][0]["strength_change_points"]==5
