from datetime import datetime, timedelta, timezone
import json
import pytest
from cbb import rows_from_payload, merge_rows, fit, predict, backtest, append_pregame, grade, write_csv

UTC=timezone.utc

def game(i, day, home='1', away='2', hs=75, ats=65, completed=True, neutral=False):
    return {'game_id':str(i),'tip_utc':day.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'season':day.year + int(day.month>=7),'home_id':home,'away_id':away,
        'home':home,'away':away,'neutral':int(neutral),
        'home_score':hs if completed else '', 'away_score':ats if completed else '',
        'completed':int(completed),'first_final_observed_utc':(day+timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ') if completed else '',
        'last_observed_utc':(day+timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')}

def records():
    base=datetime(2025,11,3,18,tzinfo=UTC)
    games=[]
    for i in range(45):
        day=base+timedelta(days=i)
        games.append(game(i,day,hs=82 if i%2==0 else 67,ats=67 if i%2==0 else 82,
                          home='1' if i%2==0 else '2',away='2' if i%2==0 else '1',neutral=i%6==0))
    return games

def test_parse():
    payload={'events':[{'id':'42','date':'2025-11-04T18:00:00Z','status':{'type':{'completed':True}},
                'competitions':[{'neutralSite':True,'competitors':[
                    {'homeAway':'home','team':{'id':'1','name':'Home'},'score':'80'},
                    {'homeAway':'away','team':{'id':'2','name':'Away'},'score':'60'}]}]}]}
    rows=rows_from_payload(payload,'2025-11-04T22:00:00Z')
    assert rows[0]['season']==2026 and rows[0]['neutral']==1
    assert rows[0]['first_final_observed_utc']=='2025-11-04T22:00:00Z'
    with pytest.raises(ValueError):
        rows_from_payload({'events':payload['events']*2},'2025-11-04T22:00:00Z')


def test_merge_first_observed_and_revision():
    old=game(1,datetime(2025,11,3,18,tzinfo=UTC))
    new={**old,'first_final_observed_utc':'2026-11-01T00:00:00Z','last_observed_utc':'2026-11-01T00:00:00Z'}
    result=merge_rows([old],[new])
    assert result[0]['first_final_observed_utc']==old['first_final_observed_utc']
    with pytest.raises(ValueError,match='revision'):
        merge_rows([old],[{**new,'home_score':91}])


def test_asof_and_low_sample():
    rows=records()
    cutoff=datetime(2025,11,19,0,tzinfo=UTC)
    fit_result=fit(rows,cutoff)
    assert fit_result['n_games']==16
    assert fit_result['training_latest_tip'] < '2025-11-19T00:00:00Z'
    p=predict(fit_result,rows[-1])
    assert p['status']=='RESEARCH_ONLY'
    assert 0<p['home_win_probability_uncalibrated']<1
    assert fit_result['season']==2026
    for r in rows:r['first_final_observed_utc']='2026-11-01T00:00:00Z'
    with pytest.raises(ValueError):fit(rows,cutoff)
    assert fit(rows,cutoff,retrospective=True)['n_games']==16


def test_replay_is_chronological():
    result=backtest(records(),test_season=2026)
    report=result['report']
    assert report['all_games']['n']>10
    assert report['established_teams']['n']>5
    assert report['ats_graded']==0
    assert all(x['training_latest_tip']<x['cutoff_utc'] for x in result['predictions'])


def test_freeze_and_grade(tmp_path):
    rows=records()
    tip=datetime(2025,12,20,18,tzinfo=UTC)
    rows.append(game(999,tip,completed=False))
    now=datetime(2025,12,19,0,tzinfo=UTC)
    ledger=tmp_path/'ledger.jsonl'
    assert append_pregame(rows,ledger,now)['captured']==1
    assert append_pregame(rows,ledger,now)['captured']==0
    rows[-1]=game(999,tip,hs=77,ats=62)
    final=grade(rows,ledger,datetime(2025,12,21,0,tzinfo=UTC))
    assert final['all_games']['n']==1
    assert final['ats_graded']==0
    with ledger.open('a') as f:
        f.write(json.dumps({**json.loads(ledger.read_text().splitlines()[0]),'captured_utc':'2025-12-21T00:00:00Z'})+'\n')
    assert grade(rows,ledger,datetime(2025,12,22,0,tzinfo=UTC))['all_games']['n']==1


def test_neutral_home_advantage():
    rows=records()
    model=fit(rows,datetime(2025,12,20,0,tzinfo=UTC))
    g=game('future',datetime(2025,12,22,0,tzinfo=UTC),completed=False)
    h=predict(model,g)
    n=predict(model,{**g,'neutral':1})
    assert abs((h['pred_margin']-n['pred_margin'])-model['beta'][1])<0.05
