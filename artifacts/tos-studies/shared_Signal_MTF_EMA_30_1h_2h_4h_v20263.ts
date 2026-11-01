#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/
def agg2 = AggregationPeriod.THIRTY_MIN;
def agg3 = AggregationPeriod.HOUR;
def agg4 = AggregationPeriod.TWO_HOURS;
def agg5 = AggregationPeriod.FOUR_HOURS;
def agg6 = AggregationPeriod.DAY;
input showTrendBubbles = yes;
input show30mArrows = no;
input show1hArrows = no;
input show2hArrows = no;
input show4hArrows = no;
input show30mBubbles = yes;
input show1hBubbles = yes;
input show2hBubbles = yes;
input show4hBubbles = yes;
input show30mCP = yes;
input show1hCP = yes;
input show2hCP = yes;
input show4hCP = yes;
# ===== Limit ARROWS + BUBBLES to last N RTH sessions (works with Extended Hours ON) =====
input limitSignalsToLastNSessions = yes;
input sessionsBack = 1;   # 1 = today RTH only, 2 = today + prior RTH, etc.
input rthStartTime = 0500;
input rthEndTime   = 1600;
def inRTH = SecondsFromTime(rthStartTime) >= 0 and SecondsTillTime(rthEndTime) > 0;
def newRTHSession = inRTH and !inRTH[1];
def sessIndex = CompoundValue(1, sessIndex[1] + (if newRTHSession then 1 else 0), 0);
def lastSessIndex = HighestAll(sessIndex);
# Shows signals on bars belonging to the last N RTH sessions (includes overnight bars that belong to those sessions)
def showRecent = if limitSignalsToLastNSessions
                 then sessIndex >= (lastSessIndex - (sessionsBack - 1))
                 else 1;
# If you ONLY want arrows/bubbles during RTH (not overnight), use this instead:
# def showRecent = if limitSignalsToLastNSessions
#                  then inRTH and sessIndex >= (lastSessIndex - (sessionsBack - 1))
#                  else 1;
def bubbleOK = showTrendBubbles and showRecent;
DefineGlobalColor("C", CreateColor(0, 175, 175));
DefineGlobalColor("P", CreateColor(190,0,190));
DefineGlobalColor("CALL", Color.CYAN);
DefineGlobalColor("PUT", Color.MAGENTA);
# =========================
# MTF EMA CROSSES (Arrows) — gated by showRecent
# =========================
# AGG2-30M
def EMA9AGG2 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg2), 9);
def EMA20AGG2 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg2), 20);
plot UP920AGG2 = if showRecent and (EMA9AGG2 crosses above EMA20AGG2) then low else Double.NaN;
UP920AGG2.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP920AGG2.SetLineWeight(5);
UP920AGG2.SetDefaultColor(Color.CYAN);
plot DN920AGG2 = if showRecent and (EMA9AGG2 crosses below EMA20AGG2) then high else Double.NaN;
DN920AGG2.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN920AGG2.SetLineWeight(5);
DN920AGG2.SetDefaultColor(Color.MAGENTA);
# AGG3-1H
def EMA9AGG3 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg3), 9);
def EMA20AGG3 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg3), 20);
plot UP920AGG3 = if showRecent and (EMA9AGG3 crosses above EMA20AGG3) then low else Double.NaN;
UP920AGG3.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP920AGG3.SetLineWeight(5);
UP920AGG3.SetDefaultColor(Color.CYAN);
plot DN920AGG3 = if showRecent and (EMA9AGG3 crosses below EMA20AGG3) then high else Double.NaN;
DN920AGG3.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN920AGG3.SetLineWeight(5);
DN920AGG3.SetDefaultColor(Color.MAGENTA);
# AGG4-2H
def EMA9AGG4 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg4), 9);
def EMA20AGG4 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg4), 20);
plot UP920AGG4 = if showRecent and (EMA9AGG4 crosses above EMA20AGG4) then low else Double.NaN;
UP920AGG4.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP920AGG4.SetLineWeight(5);
UP920AGG4.SetDefaultColor(Color.CYAN);
plot DN920AGG4 = if showRecent and (EMA9AGG4 crosses below EMA20AGG4) then high else Double.NaN;
DN920AGG4.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN920AGG4.SetLineWeight(5);
DN920AGG4.SetDefaultColor(Color.MAGENTA);
# AGG5-4H
def EMA9AGG5 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg5), 9);
def EMA20AGG5 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg5), 20);
plot UP920AGG5 = if showRecent and (EMA9AGG5 crosses above EMA20AGG5) then low else Double.NaN;
UP920AGG5.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP920AGG5.SetLineWeight(5);
UP920AGG5.SetDefaultColor(Color.CYAN);
plot DN920AGG5 = if showRecent and (EMA9AGG5 crosses below EMA20AGG5) then high else Double.NaN;
DN920AGG5.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN920AGG5.SetLineWeight(5);
DN920AGG5.SetDefaultColor(Color.MAGENTA);
# =========================
# CLOUD TREND MTF
# =========================
def MTU2 = ExpAverage(close(period = agg2), 9) is greater than or equal to ExpAverage(close(period = agg2), 20);
def MTD2 = ExpAverage(close(period = agg2), 9) is less than or equal to ExpAverage(close(period = agg2), 20);
def MTU3 = ExpAverage(close(period = agg3), 9) is greater than or equal to ExpAverage(close(period = agg3), 20);
def MTD3 = ExpAverage(close(period = agg3), 9) is less than or equal to ExpAverage(close(period = agg3), 20);
def MTU4 = ExpAverage(close(period = agg4), 9) is greater than or equal to ExpAverage(close(period = agg4), 20);
def MTD4 = ExpAverage(close(period = agg4), 9) is less than or equal to ExpAverage(close(period = agg4), 20);
def MTU5 = ExpAverage(close(period = agg5), 9) is greater than or equal to ExpAverage(close(period = agg5), 20);
def MTD5 = ExpAverage(close(period = agg5), 9) is less than or equal to ExpAverage(close(period = agg5), 20);
def MTU6 = ExpAverage(close(period = agg6), 9) is greater than or equal to ExpAverage(close(period = agg6), 20);
def MTD6 = ExpAverage(close(period = agg6), 9) is less than or equal to ExpAverage(close(period = agg6), 20);
# =========================
# Hide/Show Arrows (your toggles still work)
# =========================
UP920AGG2.SetHiding(!show30mArrows);
DN920AGG2.SetHiding(!show30mArrows);
UP920AGG3.SetHiding(!show1hArrows);
DN920AGG3.SetHiding(!show1hArrows);
UP920AGG4.SetHiding(!show2hArrows);
DN920AGG4.SetHiding(!show2hArrows);
UP920AGG5.SetHiding(!show4hArrows);
DN920AGG5.SetHiding(!show4hArrows);
# =========================
# MTF BUBBLES — gated by bubbleOK (which includes showRecent)
# =========================
# AGG2-30m
AddChartBubble(bubbleOK and show30mBubbles and show30mCP and UP920AGG2 and MTD3, low,  "C30",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show30mBubbles and show30mCP and DN920AGG2 and MTU3, high, "P30",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show30mBubbles and UP920AGG2 and MTU3, low,              "CALL30", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show30mBubbles and DN920AGG2 and MTD3, high,             "PUT30",  GlobalColor("PUT"),  yes);
# AGG3-1h
AddChartBubble(bubbleOK and show1hBubbles and show1hCP and UP920AGG3 and MTD4, low,  "C1H",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show1hBubbles and show1hCP and DN920AGG3 and MTU4, high, "P1H",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show1hBubbles and UP920AGG3 and MTU4, low,              "CALL1H", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show1hBubbles and DN920AGG3 and MTD4, high,             "PUT1H",  GlobalColor("PUT"),  yes);
# AGG4-2h
AddChartBubble(bubbleOK and show2hBubbles and show2hCP and UP920AGG4 and MTD5, low,  "C2H",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show2hBubbles and show2hCP and DN920AGG4 and MTU5, high, "P2H",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show2hBubbles and UP920AGG4 and MTU5, low,              "CALL2H", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show2hBubbles and DN920AGG4 and MTD5, high,             "PUT2H",  GlobalColor("PUT"),  yes);
# AGG5-4h
AddChartBubble(bubbleOK and show4hBubbles and show4hCP and UP920AGG5 and MTD6, low,  "C4H",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show4hBubbles and show4hCP and DN920AGG5 and MTU6, high, "P4H",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show4hBubbles and UP920AGG5 and MTU6, low,              "CALL4H", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show4hBubbles and DN920AGG5 and MTD6, high,             "PUT4H",  GlobalColor("PUT"),  yes);