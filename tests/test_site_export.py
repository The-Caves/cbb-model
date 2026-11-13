"""Static website must not create hindsight picks or unsupported market recommendations."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cbb import VERSION
from site_export import export_payload, forecasts, valid_frozen, export_site, matchup_research_forecasts

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

def test_reliability_overlay_does_not_rewrite_frozen_forecast(tmp_path):
    known={'1':'Verified Home','2':'Verified Away'}
    ledger=tmp_path/'frozen.jsonl'
    original=prediction()
    ledger.write_text(json.dumps(original)+'\n')
    payload=export_payload([game()],ledger,NOW,directory=known)
    p=payload['games'][0]['projection']
    assert p['margin']==10
    assert p['reliability']['status']=='PASS_LOW_CURRENT_SAMPLE'
    assert p['reliability']['current_season_min_games']==2
    assert p['reliability']['margin_mae_points'] is None
    assert p['reliability']['ats_edge_validated'] is False
    assert p['reliability']['lineup_availability_verified'] is False
    assert json.loads(ledger.read_text())==original

def test_pregame_shadow_is_separate_not_promoted(tmp_path):
    main=tmp_path/'main.jsonl'
    main.write_text(json.dumps(prediction())+'\n')
    candidate={**prediction(),'model':'CBB_D1_PRIOR30_SHADOW_0_1',
               'validation':'SHADOW_UNVALIDATED',
               'live_champion_changed':False,'pred_home':71.0,
               'pred_away':76.0,'pred_margin':-5.0,'pred_total':147.0}
    shadow=tmp_path/'shadow.jsonl'
    shadow.write_text(json.dumps(candidate)+'\n')
    out=export_payload([game()],main,NOW,directory={'1':'H','2':'A'},shadow_ledger=shadow)
    g=out['games'][0]
    assert g['projection']['margin']==10
    assert g['shadow_projection']['margin']==-5
    assert g['shadow_projection']['winner_disagrees']
    assert g['shadow_projection']['never_betting_advice']
    assert json.loads(main.read_text())==prediction()
    late={**candidate,'captured_utc':(TIP+timedelta(seconds=1)).isoformat().replace('+00:00','Z')}
    shadow.write_text(json.dumps(late)+'\n')
    assert export_payload([game()],main,NOW,directory={'1':'H','2':'A'},shadow_ledger=shadow)['games'][0]['shadow_projection'] is None


def test_v04_published_only_when_frozen_before_tip_and_graded_by_version(tmp_path):
    from cbb import ACTIVE_LIVE_VERSION
    old=prediction()
    latest={**old,'model':ACTIVE_LIVE_VERSION,
            'captured_utc':(NOW+timedelta(hours=2)).isoformat().replace('+00:00','Z'),
            'pred_home':85,'pred_away':69,'pred_margin':16,'pred_total':154,
            'home_id':'1','away_id':'2'}
    ledger=tmp_path/'predictions.jsonl'
    ledger.write_text(json.dumps(old)+'\n'+json.dumps(latest)+'\n')
    before=export_payload([game()],ledger,NOW+timedelta(hours=3),directory={'1':'H','2':'A'})
    assert before['games'][0]['projection']['model']==ACTIVE_LIVE_VERSION
    assert before['games'][0]['projection']['margin']==16
    finished=export_payload([game(completed=True)],ledger,TIP+timedelta(days=1),directory={'1':'H','2':'A'})
    versions=finished['performance']['prospective']['d1_by_model']
    assert set(versions)=={ACTIVE_LIVE_VERSION}
    assert versions[ACTIVE_LIVE_VERSION]['n']==1
    assert ledger.read_text().count('\n')==2
    # A snapshot first created after tipoff must never supersede earlier predictions.
    late={**latest,'captured_utc':(TIP+timedelta(seconds=1)).isoformat().replace('+00:00','Z')}
    ledger.write_text(json.dumps(old)+'\n'+json.dumps(late)+'\n')
    after=export_payload([game(completed=True)],ledger,TIP+timedelta(days=1),directory={'1':'H','2':'A'})
    assert after['games'][0]['projection']['model']==VERSION
    assert VERSION in after['performance']['prospective']['d1_by_model']


def test_pretip_market_quote_visible_only_when_observed_and_strictly_graded(tmp_path):
    from market_capture import append_pretip
    g=game()
    forecast={**prediction(),'home_id':'1','away_id':'2'}
    frozen=tmp_path/'predictions.jsonl'
    frozen.write_text(json.dumps(forecast)+'\n')
    market=tmp_path/'market.jsonl'
    payload={'events':[{'id':'100','date':g['tip_utc'],'competitions':[{
        'competitors':[
            {'homeAway':'home','team':{'id':'1','displayName':'Home University'}},
            {'homeAway':'away','team':{'id':'2','displayName':'Away College'}}],
        'odds':[{'provider':{'id':'55','name':'ESPN BET'},'spread':6.5,
                 'overUnder':140.5,
                 'homeTeamOdds':{'favorite':True},
                 'awayTeamOdds':{'favorite':False}}]}]}]}
    observed=(NOW+timedelta(hours=3)).isoformat().replace('+00:00','Z')
    assert append_pretip(payload,observed,market)['captured']==1
    kwargs=dict(directory={'1':'H','2':'A'},market_ledger=market)
    before=export_payload([g],frozen,NOW+timedelta(hours=2),**kwargs)
    assert before['games'][0]['market_spread'] is None
    assert before['performance']['market_as_observed']['overall']['ats']['n']==0
    after=export_payload([g],frozen,NOW+timedelta(hours=4),**kwargs)
    assert after['games'][0]['market_provider']=='ESPN BET'
    assert after['games'][0]['market_spread']==-6.5
    assert after['games'][0]['market_spread_edge']==3.5
    assert after['games'][0]['market_total_edge']==9.5
    graded=export_payload([game(completed=True)],frozen,
        TIP+timedelta(days=1),**kwargs)
    assert graded['performance']['market_as_observed']['overall']['ats']['w']==1
    assert graded['performance']['market_as_observed']['overall']['ou']['w']==1
    assert graded['performance']['prospective']['ats_graded']==1
    assert graded['performance']['prospective']['over_under_graded']==1
    assert graded['performance']['historical_odds_available'] is False
    assert graded['performance']['pretip_market_observations_available']
    assert json.loads(frozen.read_text())==forecast

def test_unverified_market_not_displayed_for_unverified_school(tmp_path):
    from market_capture import append_pretip
    p={**prediction(),'home_id':'1','away_id':'2'}
    f=tmp_path/'p.jsonl';f.write_text(json.dumps(p)+'\n')
    market=tmp_path/'quotes.jsonl'
    payload={'events':[{'id':'100','date':TIP.isoformat().replace('+00:00','Z'),
       'competitions':[{'competitors':[
          {'homeAway':'home','team':{'id':'1'}},
          {'homeAway':'away','team':{'id':'2'}}],
       'odds':[{'provider':{'name':'Real Source'},'spread':6.5,'overUnder':145.5,
                'homeTeamOdds':{'favorite':True},
                'awayTeamOdds':{'favorite':False}}]}]}]}
    append_pretip(payload,NOW.isoformat().replace('+00:00','Z'),market)
    out=export_payload([game()],f,NOW+timedelta(hours=1),
                       directory={'1':'H'},market_ledger=market)
    assert out['games'][0]['projection'] is None
    assert out['games'][0]['market_spread'] is None
    assert out['performance']['market_as_observed']['matched_games']==0


def test_separate_matchup_research_is_pretip_and_never_replaces_champion(tmp_path):
    from cbb import ACTIVE_LIVE_VERSION
    from matchup_v05 import VERSION_BY_VARIANT
    g=game(completed=False)
    ledger=tmp_path/"champion.jsonl"
    research=tmp_path/"matchup.jsonl"
    champion={**prediction(),"model":ACTIVE_LIVE_VERSION}
    ledger.write_text(json.dumps(champion)+"\n")
    rec={"game_id":"100","tip_utc":g["tip_utc"],"home_id":"1","away_id":"2",
         "captured_utc":NOW.isoformat().replace("+00:00","Z"),
         "training_latest_tip":(NOW-timedelta(days=1)).isoformat().replace("+00:00","Z"),
         "model":VERSION_BY_VARIANT["interaction"],"variant":"interaction",
         "champion_version":ACTIVE_LIVE_VERSION,"base_prior_weight":.15,
         "pred_home":78.,"pred_away":72.,"pred_margin":6.,"pred_total":150.}
    research.write_text(json.dumps(rec)+"\n")
    output=export_payload([g],ledger,NOW,directory={'1':'Home','2':'Away'},
                          matchup_ledger=research)
    row=output["games"][0]
    assert row["projection"]["margin"]==10
    assert row["projection"]["model"]==ACTIVE_LIVE_VERSION
    assert len(row["matchup_research"])==1
    assert row["matchup_research"][0]["model"]!=ACTIVE_LIVE_VERSION
    assert row["matchup_research"][0]["research_only"] is True
    assert row["matchup_research"][0]["betting_edge_validated"] is False
    # A hindsight record must not turn into a research projection.
    rec["captured_utc"]=(TIP+timedelta(seconds=2)).isoformat().replace("+00:00","Z")
    research.write_text(json.dumps(rec)+"\n")
    bad=export_payload([g],ledger,NOW,directory={'1':'Home','2':'Away'},
                       matchup_ledger=research)["games"][0]
    assert bad["matchup_research"]==[]
    # Research is not rendered as an official pick if original was unavailable.
    absent=export_payload([g],tmp_path/"does-not-exist.jsonl",NOW,
                          directory={'1':'Home','2':'Away'},
                          matchup_ledger=research)["games"][0]
    assert absent["projection"] is None
    assert absent["matchup_research"]==[]
