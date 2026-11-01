import test from 'node:test';
import assert from 'node:assert/strict';
import {jevLabel,newsOf} from './momxCells.js';
import {storedNewsToRowNews,newsSymbolKey} from './momxNewsFeed.js';

test('local neural sentiments carry AI provenance and positive, negative, neutral labels',()=>{
 for(const [label,text,tone] of [['bullish','Positive','up'],['bearish','Negative','down'],['not_important','Neutral','flat']]){
  const reading={label,p:.95,provider:'local-neural',model:'financial-distilroberta-int8',judgedAt:'2026-10-30T12:00:00Z'};
  const v=jevLabel(reading);assert.equal(v.text,`AI: ${text} 95%`);assert.equal(v.tone,tone);assert.match(v.title,/not the chance of a stock move/);
  const story=storedNewsToRowNews({headline:'Microsoft earnings announcement',publishedAt:'2026-10-30T11:59:00Z',aiNews:reading});
  assert.equal(newsOf({news:story},Date.parse('2026-10-30T12:00:00Z')).jev.text,v.text);
 }
 assert.equal(jevLabel({provider:'local-neural',label:'bullish',p:1.1}),null);
 assert.equal(jevLabel({provider:'local-neural',label:'bullish',p:1}).text,'AI: Positive ≥99%');
 assert.match(jevLabel({provider:'local-neural',label:'not_important',raw:'bullish',p:.55,why:'AI only 55% sure'}).text,/Uncertain/);
});

test('news requests keep one identity across ticker-ranking changes',()=>{
 assert.equal(newsSymbolKey(['MSFT','AAPL','MSFT']),newsSymbolKey([' aapl ','msft']));
 assert.equal(newsSymbolKey(['MSFT','AAPL']),'AAPL,MSFT');
 assert.notEqual(newsSymbolKey(['TSLA']),newsSymbolKey(['AAPL']));
});
