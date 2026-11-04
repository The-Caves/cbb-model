"""Guardrail tests: no fabricated team features, no future scores, no promotion."""
from datetime import datetime,timezone,timedelta
import pytest
from efficiency_challenger import usable,fit_efficiency,predict,backtest

UTC=timezone.utc
def game(i,day,h='1',a='2',hs=90,ats=60):
    dt=datetime(2025,11,1,tzinfo=UTC)+timedelta(days=day)
    return {'game_id':str(i),'tip_utc':dt.isoformat().replace('+00:00','Z'),
            'season':2026,'home_id':h,'away_id':a,'home':h,'away':a,
            'neutral':0,'completed':1,'home_score':hs,'away_score':ats,
            'first_final_observed_utc':(dt+timedelta(hours=5)).isoformat().replace('+00:00','Z')}
def record(g):
    return {'game_id':g['game_id'],'tip_utc':g['tip_utc'],'season':2026,
       'home_id':g['home_id'],'away_id':g['away_id'],
       'source_captured_utc':'2026-11-04T12:00:00Z','raw_sha256':'a'*64,
       'home':{'points':g['home_score'],'off_rtg':130,'pace_est':69},
       'away':{'points':g['away_score'],'off_rtg':87,'pace_est':69}}
def test_cutoffs_and_missing_directory():
    g=record(game(1,0))
    cutoff=datetime(2025,11,8,tzinfo=UTC)
    assert usable(g,cutoff,2026,{'1','2'},True)
    assert not usable(g,cutoff,2026,{'1','2'},False)
    assert not usable(g,cutoff,2026,{'1'},True)
    assert fit_efficiency([g],cutoff,2026,{'1','2'},True,min_training=2) is None
def test_temporal_training_and_opposing_venue():
    rows=[record(game(i,i,'1','2',90,60)) for i in range(32)]
    cutoff=datetime(2025,12,10,tzinfo=UTC)
    m=fit_efficiency(rows,cutoff,2026,{'1','2'},True,min_training=20)
    assert m['training_n']==32 and m['last_tip']<iso(cutoff)
    p=predict(m,game(100,36,'1','2'))
    r=predict(m,game(101,36,'2','1'))
    assert p['pred_margin']>2 and r['pred_margin']<0
    neutral=predict(m,{**game(102,36,'1','2'),'neutral':1})
    assert p['pred_margin']-neutral['pred_margin']==pytest.approx(3.5,abs=.02)
def test_insufficient_coverage_never_promoted():
    gs=[game(i,i) for i in range(20)]
    result=backtest(gs,[record(g) for g in gs],{'1','2'},season=2026,min_training=250,min_eval=100)
    assert result['status']=='INSUFFICIENT_MATCHED_HISTORICAL_EVALUATIONS'
    assert result['candidate']['n']==0 and not result['challenger_promoted']
