import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { BOT_LEVEL_OPTIONS, filterBotLevel } from './botLevels.js';
import { botReport, BOT_RULE_HELP } from './momxBot.js';
test('Level dropdown filters all aggregates and preserves original records',()=>{
  const days=[{day:'2026-10-30',trades:[{rule:'level1',symbol:'X',entry:1,exitPrice:.5,status:'closed'},
    {rule:'level2',symbol:'Y',entry:1,exitPrice:2,status:'closed'},{rule:'mom',symbol:'Z',entry:1,exitPrice:3,status:'closed'}]}];
  assert.equal(BOT_LEVEL_OPTIONS.length,4);
  const selected=filterBotLevel(days,'level1'),r=botReport(selected);
  assert.equal(r.rows.length,1);assert.equal(r.rows[0].rule,'level1');
  assert.equal(r.total.losses,1);assert.equal(r.total.wins,0);
  assert.equal(days[0].trades.length,3);assert.equal(filterBotLevel(days,'all'),days);
  assert.equal(filterBotLevel(days,'invalid'),days);
  assert.equal(botReport(filterBotLevel(days,'level3')).rows.length,0);
  assert.ok(BOT_RULE_HELP.level3.needs.includes('Skittles'));
});
test('All trades selection is a labelled dropdown and uses the selected book for history too',()=>{
  const s=fs.readFileSync(new URL('./BotPanel.jsx',import.meta.url),'utf8');
  assert.match(s,/aria-label="Level strategy"/);
  assert.match(s,/filterBotLevel\(days, levelStrategy\)/);
  assert.match(s,/botPeriodMatrix\(pick\(history/);
});
