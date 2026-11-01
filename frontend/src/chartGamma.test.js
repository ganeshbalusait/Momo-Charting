import test from "node:test";
import assert from "node:assert/strict";
import { buildChartGamma, chartGammaEmptyMessage, ChartGammaPrimitive, formatGammaDollars } from "./chartGamma.js";

const row = (side, extra = {}) => ({ symbol: `SPY   261030${side === "CALL" ? "C" : "P"}00100000`,
  side, expiry: "2026-10-30", strike: 100, gamma: 0.01, volume: 50, open_interest: 100, ...extra });
const build = (rows, extra = {}) => buildChartGamma({ rows, symbol: "SPY", spot: 100, mode: "volume", ...extra });

test("OI dollar gamma uses 100-share contracts and a 1% move, netting puts against calls", () => {
  const model = build([row("CALL"), row("PUT", { open_interest: 40 })], { mode: "oi" });
  assert.deepEqual(model.levels[0], { strike: 100, call: 10000, put: 4000, net: 6000, color: "#ffd400" });
  assert.equal(model.priced, 2);
});
test("volume is a separate cumulative proxy, not added to open interest", () => {
  const model = build([row("CALL"), row("PUT", { volume: 80 })]);
  assert.equal(model.levels[0].net, -3000);
  assert.equal(model.levels[0].color, "#ffffff");
  assert.equal(build([row("CALL", { contractMultiplier: 10 })]).levels[0].net, 500);
  assert.equal(build([row("CALL")], { spot: 200 }).levels[0].net, 20000);
});
test("missing greeks/counts are excluded and disclosed, real zero remains valid", () => {
  const rows = [row("CALL", { gamma: null }), row("PUT", { gamma: 0 })];
  const model = build(rows);
  assert.equal(model.eligible, 2); assert.equal(model.priced, 1); assert.equal(model.missing, 1);
  assert.deepEqual(model.levels, []);
  for (const extra of [{ gamma: -1 }, { volume: null }, { volume: "" }, { volume: -1 }, { gamma: Infinity }, { multiplier: 0 }]) {
    assert.equal(build([row("CALL", extra)]).missing, 1);
  }
  assert.deepEqual(build([row("CALL")], { spot: 0 }).levels, []);
});
test("contract ownership prevents stale cross-ticker and adjusted-root contamination", () => {
  assert.equal(build([row("CALL")], { symbol: "AAPL" }).levels.length, 0);
  assert.equal(build([row("CALL", { symbol: "SPY1  261030C00100000" })]).levels.length, 0);
  assert.equal(build([row("CALL", { symbol: "" })]).levels.length, 0);
});
test("expiry selection and duplicate snapshots never combine or double count contracts", () => {
  const rows = [row("CALL"), row("CALL", { volume: 60 }), row("PUT", { expiry: "2026-11-06", volume: 500 })];
  const first = build(rows, { selectedExpiry: "2026-10-30" });
  assert.equal(first.eligible, 1); assert.equal(first.levels[0].net, 6000);
  assert.equal(build(rows, { expiry: "2026-11-06" }).levels[0].net, -50000);
  assert.equal(build(rows, { expiry: "2020-01-01", selectedExpiry: "2026-11-06" }).expiry, "2026-11-06");
});
test("largest absolute concentrations are bounded and cancellation does not invent a level", () => {
  assert.equal(build([row("CALL"), row("PUT")]).levels.length, 0);
  const rows = Array.from({ length: 20 }, (_, i) => row("CALL", { strike: 100 + i, volume: i + 1 }));
  assert.equal(build(rows, { limit: 99 }).levels.length, 12);
  assert.equal(build(rows, { limit: 2 }).levels[0].strike, 119);
  assert.equal(formatGammaDollars(-10540000000), "−$10.54B");
});
test("native overlay follows price coordinates, clips offscreen levels, and detaches", () => {
  const model = build([row("CALL"), row("PUT", { strike: 110, volume: 200 })]);
  const primitive = new ChartGammaPrimitive(model);
  let offset = 0, updates = 0;
  primitive.attached({ series: { priceToCoordinate: price => price === 110 ? -20 : 50 + offset }, requestUpdate: () => updates++ });
  const boxes = [], texts = [];
  const ctx = { save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, strokeRect() {}, setLineDash() {},
    fillRect: (...args) => boxes.push(args), measureText: text => ({ width: text.length * 6 }), fillText: (...args) => texts.push(args) };
  const target = { useMediaCoordinateSpace: fn => fn({ context: ctx, mediaSize: { width: 300, height: 200 } }) };
  primitive.paneViews()[0].renderer().draw(target);
  assert.equal(updates, 1); assert.equal(texts.length, 3); assert.equal(boxes[0][1], 45);
  offset = 20; boxes.length = 0;
  primitive.draw(target); assert.equal(boxes[0][1], 65);
  primitive.detached(); boxes.length = 0; primitive.draw(target); assert.equal(boxes.length, 0);
});

test("OI is the initial basis even when the snapshot has zero volume", () => {
  const model = buildChartGamma({ rows: [row("CALL", { volume: 0 })], symbol: "SPY", spot: 100 });
  assert.equal(model.mode, "oi"); assert.equal(model.levels[0].net, 10000);
  assert.equal(model.emptyReason, ""); assert.equal(chartGammaEmptyMessage(model), "");
});

test("zero volume is distinguished from missing gamma and offsetting exposures", () => {
  const empty = build([row("CALL", { volume: 0 })]);
  assert.equal(empty.emptyReason, "zero-volume");
  assert.match(chartGammaEmptyMessage(empty), /zero option volume/);
  assert.equal(build([row("CALL", { volume: null })]).emptyReason, "missing-inputs");
  assert.equal(build([row("CALL", { gamma: 0 })]).emptyReason, "zero-gamma");
  assert.equal(build([row("CALL"), row("PUT")]).emptyReason, "net-zero");
  assert.equal(build([row("CALL", { gamma: 0 }), row("PUT", { volume: 0 })]).emptyReason, "zero-gamma");
  assert.equal(build([row("CALL", { open_interest: 0 })], { mode: "oi" }).emptyReason, "zero-oi");
});

test("a later nonzero volume snapshot clears the empty state without mixing in OI", () => {
  assert.equal(build([row("CALL", { volume: 0 })]).levels.length, 0);
  const active = build([row("CALL", { volume: 25, open_interest: 9999 })]);
  assert.equal(active.emptyReason, ""); assert.equal(active.levels[0].net, 2500);
});

test("boxed components show put share and white/gold amounts inside the pane", () => {
  const primitive = new ChartGammaPrimitive(build([row("CALL", { volume: 265 }), row("PUT", { volume: 629 })]));
  primitive.attached({series:{priceToCoordinate:()=>70},requestUpdate(){}});
  const texts=[], outlines=[];
  const ctx={save(){},restore(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},setLineDash(){},fillRect(){},
    strokeRect(...args){outlines.push({color:this.strokeStyle,args});},
    measureText:text=>({width:text.length*6}),fillText:(...args)=>texts.push(args)};
  primitive.draw({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:400,height:200}})});
  assert.ok(texts.some(([text])=>text==='P 70% −$62.90K'));
  assert.ok(texts.some(([text])=>text==='C +$26.50K'));
  assert.ok(texts.some(([text])=>text.includes('−$36.40K')));
  assert.ok(outlines.some(box=>box.color==='#ffffff'));
  assert.ok(outlines.some(box=>box.color==='#ffd400'));
  assert.ok(outlines.every(({args:[x,y,w,h]})=>x>=0&&x+w<=400&&y>=0&&y+h<=200));
  assert.equal(primitive.paneViews(),primitive.paneViews());
});

test("optional glow uses white for negative exposure and amber for positive exposure", () => {
  const stops=[];
  const ctx={save(){},restore(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},setLineDash(){},fillRect(){},strokeRect(){},
    measureText:text=>({width:text.length*6}),fillText(){},createLinearGradient:()=>({addColorStop:(...args)=>stops.push(args)})};
  const target={useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:400,height:200}})};
  for(const [side,rgb] of [['CALL','215,132,0'],['PUT','215,220,230']]) {
    stops.length=0;
    const primitive=new ChartGammaPrimitive(build([row(side)]));
    primitive.attached({series:{priceToCoordinate:()=>70},requestUpdate(){}});
    primitive.draw(target);
    assert.ok(stops.some(([at,color])=>at===0.5 && color===`rgba(${rgb},0.68)`));
    assert.ok(stops.some(([at,color])=>at===0.35 && color===`rgba(${rgb},0.48)`));
    primitive.glow=false;stops.length=0;primitive.draw(target);assert.equal(stops.length,0);
  }
});

test("hiding text preserves compact right-side clouds and strike guides through resize", () => {
  const fills=[],texts=[],lines=[];
  const ctx={save(){},restore(){},beginPath(){},moveTo(){},lineTo:(...a)=>lines.push(a),stroke(){},setLineDash(){},
    fillRect:(...a)=>fills.push(a),strokeRect(){},measureText:t=>({width:t.length*6}),fillText:t=>texts.push(t),
    createLinearGradient:()=>({addColorStop(){}})};
  const primitive=new ChartGammaPrimitive(build([row('PUT')]),{labels:false});
  primitive.attached({series:{priceToCoordinate:()=>70},requestUpdate(){}});
  for(const width of [390,1200]){
    fills.length=0;lines.length=0;
    primitive.draw({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width,height:200}})});
    assert.equal(texts.length,0);
    const [left,,bandWidth,bandHeight]=fills[0];
    assert.ok(left > width * 0.7, "cloud must leave the candle history clear");
    assert.ok(bandWidth <= (width < 480 ? 84 : 132));
    assert.ok(bandHeight <= 22);
    assert.equal(left + bandWidth,width-8);
    assert.equal(lines[0][0],width-8);
  }
  primitive.labels=true;primitive.draw({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:1200,height:200}})});
  assert.equal(texts.length,3);
});
