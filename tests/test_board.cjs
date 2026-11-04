const test=require('node:test');
const assert=require('node:assert/strict');
const {availableDates,nextUnstartedGame,dateKey}=require('../site/app.js');
const local=(y,m,d)=>new Date(y,m-1,d,12,0,0);

test('today appears as a date even if games have not been ingested',()=>{
  const today=dateKey(local(2026,11,3));
  const games=[{tip_utc:local(2026,11,2).toISOString()},{tip_utc:local(2026,11,5).toISOString()}];
  const dates=availableDates(games,today);
  assert.ok(dates.includes(today));
  assert.deepEqual(dates,[...dates].sort());
});

test('next upcoming start excludes completed and already-started games',()=>{
  const now=Date.parse('2026-11-03T20:00:00Z');
  const games=[
    {id:'in-progress',tip_utc:'2026-11-03T19:00:00Z',completed:false},
    {id:'later',tip_utc:'2026-11-03T23:30:00Z',completed:false},
    {id:'next',tip_utc:'2026-11-03T21:00:00Z',completed:false},
    {id:'final',tip_utc:'2026-11-03T18:00:00Z',completed:true},
  ];
  assert.equal(nextUnstartedGame(games,now).id,'next');
  assert.equal(games.length,4); // Past and in-progress cards remain available
  assert.equal(nextUnstartedGame(games,Date.parse('2026-11-04T00:00:00Z')),null);
});
