#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/
def agg1 = AggregationPeriod.FIFTEEN_MIN;
def agg2 = AggregationPeriod.THIRTY_MIN;
def agg3 = AggregationPeriod.HOUR;
def agg4 = AggregationPeriod.TWO_HOURS;
def agg5 = AggregationPeriod.FOUR_HOURS;
def agg6 = AggregationPeriod.DAY;
input showTrendBubbles = yes;
input show15mArrows = no;
input show30mArrows = no;
input show1hArrows = no;
input show2hArrows = no;
input show4hArrows = no;
input show15mBubbles = yes;
input show30mBubbles = yes;
input show1hBubbles = yes;
input show2hBubbles = yes;
input show4hBubbles = yes;
input show15mCP = yes;
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
DefineGlobalColor("C", CreateColor(169,255,0));
DefineGlobalColor("P", CreateColor(255,0,106));
DefineGlobalColor("CALL", CreateColor(169,255,0)); #CreateColor(101, 241, 255));
DefineGlobalColor("PUT", CreateColor(255,0,106)); #CreateColor(255,101,255));
# =========================
# MTF EMA CROSSES (Arrows) — gated by showRecent
# =========================
# AGG2-15M
def EMA4AGG1 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg1), 4);
def EMA8AGG1 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg1), 8);
plot UP48AGG1 = if showRecent and (EMA4AGG1 crosses above EMA8AGG1) then low else Double.NaN;
UP48AGG1.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48AGG1.SetLineWeight(5);
UP48AGG1.SetDefaultColor(GlobalColor("CALL"));
plot DN48AGG1 = if showRecent and (EMA4AGG1 crosses below EMA8AGG1) then high else Double.NaN;
DN48AGG1.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48AGG1.SetLineWeight(5);
DN48AGG1.SetDefaultColor(GlobalColor("PUT"));
# AGG2-30M
def EMA4GG2 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg2), 4);
def EMA8AGG2 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg2), 8);
plot UP48AGG2 = if showRecent and (EMA4GG2 crosses above EMA8AGG2) then low else Double.NaN;
UP48AGG2.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48AGG2.SetLineWeight(5);
UP48AGG2.SetDefaultColor(GlobalColor("CALL"));
plot DN48AGG2 = if showRecent and (EMA4GG2 crosses below EMA8AGG2) then high else Double.NaN;
DN48AGG2.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48AGG2.SetLineWeight(5);
DN48AGG2.SetDefaultColor(GlobalColor("PUT"));
# AGG3-1H
def EMA4GG3 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg3), 4);
def EMA8AGG3 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg3), 8);
plot UP48AGG3 = if showRecent and (EMA4GG3 crosses above EMA8AGG3) then low else Double.NaN;
UP48AGG3.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48AGG3.SetLineWeight(5);
UP48AGG3.SetDefaultColor(GlobalColor("CALL"));
plot DN48AGG3 = if showRecent and (EMA4GG3 crosses below EMA8AGG3) then high else Double.NaN;
DN48AGG3.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48AGG3.SetLineWeight(5);
DN48AGG3.SetDefaultColor(GlobalColor("PUT"));
# AGG4-2H
def EMA4GG4 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg4), 4);
def EMA8AGG4 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg4), 8);
plot UP48AGG4 = if showRecent and (EMA4GG4 crosses above EMA8AGG4) then low else Double.NaN;
UP48AGG4.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48AGG4.SetLineWeight(5);
UP48AGG4.SetDefaultColor(GlobalColor("CALL"));;
plot DN48AGG4 = if showRecent and (EMA4GG4 crosses below EMA8AGG4) then high else Double.NaN;
DN48AGG4.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48AGG4.SetLineWeight(5);
DN48AGG4.SetDefaultColor(GlobalColor("PUT"));
# AGG5-4H
def EMA4GG5 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg5), 4);
def EMA8AGG5 = MovingAverage(AverageType.EXPONENTIAL, close(period = agg5), 8);
plot UP48AGG5 = if showRecent and (EMA4GG5 crosses above EMA8AGG5) then low else Double.NaN;
UP48AGG5.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48AGG5.SetLineWeight(5);
UP48AGG5.SetDefaultColor(GlobalColor("CALL"));
plot DN48AGG5 = if showRecent and (EMA4GG5 crosses below EMA8AGG5) then high else Double.NaN;
DN48AGG5.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48AGG5.SetLineWeight(5);
DN48AGG5.SetDefaultColor(GlobalColor("PUT"));
# =========================
# CLOUD TREND MTF
# =========================
#def MTU1 = ExpAverage(close(period = agg1), 4) is greater than or equal to ExpAverage(close(period = agg1), 8);
#def MTD1 = ExpAverage(close(period = agg1), 4) is less than or equal to ExpAverage(close(period = agg1), 8);
def MTU2 = ExpAverage(close(period = agg2), 4) is greater than or equal to ExpAverage(close(period = agg2), 8);
def MTD2 = ExpAverage(close(period = agg2), 4) is less than or equal to ExpAverage(close(period = agg2), 8);
def MTU3 = ExpAverage(close(period = agg3), 4) is greater than or equal to ExpAverage(close(period = agg3), 8);
def MTD3 = ExpAverage(close(period = agg3), 4) is less than or equal to ExpAverage(close(period = agg3), 8);
def MTU4 = ExpAverage(close(period = agg4), 4) is greater than or equal to ExpAverage(close(period = agg4), 8);
def MTD4 = ExpAverage(close(period = agg4), 4) is less than or equal to ExpAverage(close(period = agg4), 8);
def MTU5 = ExpAverage(close(period = agg5), 4) is greater than or equal to ExpAverage(close(period = agg5), 8);
def MTD5 = ExpAverage(close(period = agg5), 4) is less than or equal to ExpAverage(close(period = agg5), 8);
def MTU6 = ExpAverage(close(period = agg6), 4) is greater than or equal to ExpAverage(close(period = agg6), 8);
def MTD6 = ExpAverage(close(period = agg6), 4) is less than or equal to ExpAverage(close(period = agg6), 8);
# =========================
# Hide/Show Arrows (your toggles still work)
# =========================
UP48AGG1.SetHiding(!show15mArrows);
DN48AGG1.SetHiding(!show15mArrows);
UP48AGG2.SetHiding(!show30mArrows);
DN48AGG2.SetHiding(!show30mArrows);
UP48AGG3.SetHiding(!show1hArrows);
DN48AGG3.SetHiding(!show1hArrows);
UP48AGG4.SetHiding(!show2hArrows);
DN48AGG4.SetHiding(!show2hArrows);
UP48AGG5.SetHiding(!show4hArrows);
DN48AGG5.SetHiding(!show4hArrows);
# =========================
# MTF BUBBLES — gated by bubbleOK (which includes showRecent)
# =========================
# AGG1-15m
AddChartBubble(bubbleOK and show15mBubbles and show15mCP and UP48AGG1 and MTD2, low,  "C15",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show15mBubbles and show15mCP and DN48AGG1 and MTU2, high, "P15",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show15mBubbles and UP48AGG1 and MTU2, low,              "CALL15", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show15mBubbles and DN48AGG1 and MTD2, high,             "PUT15",  GlobalColor("PUT"),  yes);
# AGG2-30m
AddChartBubble(bubbleOK and show30mBubbles and show30mCP and UP48AGG2 and MTD3, low,  "C30",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show30mBubbles and show30mCP and DN48AGG2 and MTU3, high, "P30",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show30mBubbles and UP48AGG2 and MTU3, low,              "CALL30", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show30mBubbles and DN48AGG2 and MTD3, high,             "PUT30",  GlobalColor("PUT"),  yes);
# AGG3-1h
AddChartBubble(bubbleOK and show1hBubbles and show1hCP and UP48AGG3 and MTD4, low,  "C1H",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show1hBubbles and show1hCP and DN48AGG3 and MTU4, high, "P1H",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show1hBubbles and UP48AGG3 and MTU4, low,              "CALL1H", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show1hBubbles and DN48AGG3 and MTD4, high,             "PUT1H",  GlobalColor("PUT"),  yes);
# AGG4-2h
AddChartBubble(bubbleOK and show2hBubbles and show2hCP and UP48AGG4 and MTD5, low,  "C2H",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show2hBubbles and show2hCP and DN48AGG4 and MTU5, high, "P2H",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show2hBubbles and UP48AGG4 and MTU5, low,              "CALL2H", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show2hBubbles and DN48AGG4 and MTD5, high,             "PUT2H",  GlobalColor("PUT"),  yes);
# AGG5-4h
AddChartBubble(bubbleOK and show4hBubbles and show4hCP and UP48AGG5 and MTD6, low,  "C4H",    GlobalColor("C"),    no);
AddChartBubble(bubbleOK and show4hBubbles and show4hCP and DN48AGG5 and MTU6, high, "P4H",    GlobalColor("P"),    yes);
AddChartBubble(bubbleOK and show4hBubbles and UP48AGG5 and MTU6, low,              "CALL4H", GlobalColor("CALL"), no);
AddChartBubble(bubbleOK and show4hBubbles and DN48AGG5 and MTD6, high,             "PUT4H",  GlobalColor("PUT"),  yes);