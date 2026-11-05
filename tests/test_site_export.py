"""Static website must not create hindsight picks or unsupported market recommendations."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cbb import VERSION
from site_export import export_payload, forecasts, valid_frozen, export_site

UTC=timezone.utc
TIP=datetime(2026,11,15,23,tzinfo=UTC)
NOW=TIP-timedelta(days=1)

def game(completed=False):
    return {'game_id':'100','tip_utc':TIP.isoformat().replace('+00:00','Z'),
        'season':2027, 'home_id':'1','away_id':'2','home':'Home University','away':'Away College',
        'neutral':'0','home_score':'77' if completed else '',
        'away_score':'67' if completed else '','completed':'1' if completed else '0',
        'first_final_observed_utc':(TIP+timedelta(hours=5)).isoformat().replace('+00:00','Z') if completed else '',
        'last_observed_utc':NOW.isoformat().replace('+00:00','Z')}

def prediction():
    return {'game_id':'100','tip_utc':TIP.isoformat().replace('+00:00','Z'),
        'captured_utc':NOW.isoformat().replace('+00:00','Z'),
        'model':VERSION,'home':'Home University','away':'Away College',
        'pred_home':80.0,'pred_away':70.0,'pred_margin':10.0,'pred_total':150.0,
        'validation':'PASS_UNVALIDATED','status':'PASS_LOW_SAMPLE','home_current_games':2,
        'away_current_games':2,'training_games':900,
        'home_win_probability_uncalibrated':.78}

def test_no_forecast_means_no_model_pick(tmp_path):
    out=export_payload([game()],tmp_path/'not-real.jsonl',NOW)
    assert len(out['games'])==1
    assert out['games'][0]['projection'] is None
    assert out['games'][0]['market_spread'] is None
    assert out['performance']['historical_odds_available'] is False
    assert out['performance']['prospective']['all_games']['n']==0

def test_only_original_pre_tip_capture_can_publish(tmp_path):
    ledger=tmp_path/'predictions.jsonl'
    late={**prediction(),'captured_utc':(TIP+timedelta(minutes=1)).isoformat().replace('+00:00','Z')}
    mismatched={**prediction(),'home':'Different team'}
    wrong_model={**prediction(),'model':'Unverified model'}
    bad_score={**prediction(),'pred_margin':15}
    good=prediction()
    assert not valid_frozen(late,game())
    assert not valid_frozen(mismatched,game())
    assert not valid_frozen(wrong_model,game())
    assert not valid_frozen(bad_score,game())
    ledger.write_text('\n'.join(json.dumps(x) for x in (late,mismatched,wrong_model,bad_score,good))+'\n')
    found=forecasts(ledger,{'100':game()})
    assert found['100']['pred_margin']==10
    out=export_payload([game()],ledger,NOW,directory={'1':'Verified Home','2':'Verified Away'})
    assert out['games'][0]['projection']['home']==80
    assert out['games'][0]['projection']['status']=='PASS_LOW_SAMPLE'
    assert out['performance']['frozen_games_in_window']==1

def test_no_hindsight_grades(tmp_path):
    ledger=tmp_path/'predictions.jsonl'
    ledger.write_text(json.dumps(prediction())+'\n')
    graded=export_payload([game(completed=True)],ledger,TIP+timedelta(days=1),directory={'1':'Verified Home','2':'Verified Away'})
    assert graded['performance']['prospective']['all_games']['n']==1
    posttip={**prediction(),'captured_utc':(TIP+timedelta(minutes=1)).isoformat().replace('+00:00','Z')}
    ledger.write_text(json.dumps(posttip)+'\n')
    not_graded=export_payload([game(completed=True)],ledger,TIP+timedelta(days=1),directory={'1':'Verified Home','2':'Verified Away'})
    assert not_graded['performance']['prospective']['all_games']['n']==0
    assert not_graded['games'][0]['projection'] is None

def test_empty_site_is_honest(tmp_path):
    result=export_site(tmp_path/'missing',tmp_path/'site',NOW)
    out=json.loads((tmp_path/'site/data.json').read_text())
    assert result['games']==0
    assert result['forecasts']==0
    assert out['games']==[]
    assert out['validation']=='PASS_UNVALIDATED'

def test_discard_non_72h_capture(tmp_path):
    g=game()
    stale={**prediction(),'captured_utc':(TIP-timedelta(hours=73)).isoformat().replace('+00:00','Z')}
    assert not valid_frozen(stale,g)


def test_original_v02_forecast_survives_model_update(tmp_path):
    ledger=tmp_path/'ledger.jsonl'
    legacy={**prediction(),'model':'CBB_SCORE_RIDGE_V0_2'}
    ledger.write_text(json.dumps(legacy)+'\n')
    out=export_payload([game()],ledger,NOW,directory={'1':'Verified Home Owls','2':'Verified Away Bears'})
    assert out['games'][0]['projection']['model']=='CBB_SCORE_RIDGE_V0_2'
    assert out['games'][0]['home']=='Verified Home Owls'
    assert out['games'][0]['away']=='Verified Away Bears'
    assert out['games'][0]['projection']['home']==80
    assert len(out['teams'])==2


def test_unverified_opponent_kept_on_board_not_in_d1_scouting(tmp_path):
    ledger=tmp_path/'ledger.jsonl'
    out=export_payload([game()],ledger,NOW,directory={'1':'Verified Home Owls'})
    assert len(out['games'])==1
    assert out['games'][0]['away_division']=='OTHER_OR_UNVERIFIED'
    assert out['games'][0]['away']=='Away College'
    assert [x['id'] for x in out['teams']]==['1']
    assert out['directory']['unverified_opponents_on_board']==1


def test_new_model_freeze_uses_stable_team_ids(tmp_path):
    raw={**prediction(), 'home_id':'1','away_id':'2','home':'Old University Name',
         'away':'Previous source away label'}
    assert valid_frozen(raw, game())
    assert not valid_frozen({**raw,'home_id':'99'}, game())
    assert not valid_frozen({**raw,'away_id':'99'}, game())

def test_non_d1_prediction_hidden_on_board_without_erasing_ledger(tmp_path):
    first=game()
    other={**game(),'game_id':'101','away_id':'999','away':'Warren Wilson Owls'}
    raw=prediction()
    ledger=tmp_path/'ledger.jsonl'
    ledger.write_text(json.dumps(raw)+'\n'+json.dumps({
        **raw,'game_id':'101','away_id':'999','away':'Warren Wilson Owls'})+'\n')
    out=export_payload([first,other],ledger,NOW,
        directory={'1':'Verified Home','2':'Verified Away'})
    by={g['id']:g for g in out['games']}
    assert by['100']['verified_d1_matchup'] and by['100']['projection']
    assert not by['101']['verified_d1_matchup']
    assert by['101']['projection'] is None
    assert by['101']['hidden_unqualified_projection']
    assert out['performance']['frozen_games_in_window']==1

def test_quarantined_identity_is_not_a_valid_d1_game_or_graded(tmp_path):
    candidate={**game(completed=True), 'identity_conflict':1}
    ledger=tmp_path/'ledger.jsonl'
    ledger.write_text(json.dumps(prediction())+'\n')
    out=export_payload([candidate],ledger,TIP+timedelta(days=1),
        directory={'1':'Verified Home','2':'Verified Away'})
    g=out['games'][0]
    assert g['identity_source_under_review']
    assert not g['verified_d1_matchup']
    assert g['projection'] is None
    assert out['performance']['prospective']['verified_d1_matchups']['n']==0
