#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/
input AP2 = AggregationPeriod.FIFTEEN_MIN;
input AP3 = AggregationPeriod.THIRTY_MIN;
input AP4 = AggregationPeriod.HOUR;
input AP5 = AggregationPeriod.TWO_HOURS;
input AP6 = AggregationPeriod.FOUR_HOURS;
input AP7 = AggregationPeriod.DAY;
input showBubbles = yes;
input showCloud1 = yes;
input showCloud2 = yes;
input showCloud3 = yes;
input showCloud4 = yes;
input showCloud5 = yes;
input showCloud6 = yes;
input showCloud7 = yes;
input average = AverageType.SIMPLE;
def length = 20;
def nK = 1.5;
def nBB = 2.0;
# ===== Limit BUBBLES + CLOUDS to last N RTH sessions (works with Extended Hours ON) =====
input limitSignalsToLastNSessions = yes;
input sessionsBack = 1;   # 1 = today RTH only, 2 = today + prior RTH, etc.
input rthStartTime = 0500;
input rthEndTime   = 1600;
def inRTH = SecondsFromTime(rthStartTime) >= 0 and SecondsTillTime(rthEndTime) > 0;
def newRTHSession = inRTH and !inRTH[1];
def sessIndex = CompoundValue(1, sessIndex[1] + (if newRTHSession then 1 else 0), 0);
def lastSessIndex = HighestAll(sessIndex);
# Includes overnight bars that belong to those sessions
def showRecent = if limitSignalsToLastNSessions
                 then sessIndex >= (lastSessIndex - (sessionsBack - 1))
                 else 1;
# If you ONLY want bubbles/clouds during RTH (not overnight), use this instead:
# def showRecent = if limitSignalsToLastNSessions
#                  then inRTH and sessIndex >= (lastSessIndex - (sessionsBack - 1))
#                  else 1;
def bubbleOK = showBubbles and showRecent;
def cloudOK  = showRecent;
##KeltsMTF##
# 5 Min (current chart aggregation)
def average1 = MovingAverage(average, close, length);
def shiftMid1 = nK * MovingAverage(average, TrueRange(high, close, low), length);
def KeltMid1 = average1 + shiftMid1;
def squeeze1 = reference BollingerBands(price = close, length = length, numDevDn = nBB).upperband - KeltMid1;
def inSqueeze1 = squeeze1 <= 0;
# 15 Min
def average2 = MovingAverage(average, close(period = AP2), length);
def shiftMid2 = nK * MovingAverage(average, TrueRange(high(period = AP2), close(period = AP2), low(period = AP2)), length);
def KeltMid2 = average2 + shiftMid2;
def squeeze2 = reference BollingerBands(price = close(period = AP2), length = length, numDevDn = nBB).upperband - KeltMid2;
def inSqueeze2 = squeeze2 <= 0;
# 30 Min
def average3 = MovingAverage(average, close(period = AP3), length);
def shiftMid3 = nK * MovingAverage(average, TrueRange(high(period = AP3), close(period = AP3), low(period = AP3)), length);
def KeltMid3 = average3 + shiftMid3;
def squeeze3 = reference BollingerBands(price = close(period = AP3), length = length, numDevDn = nBB).upperband - KeltMid3;
def inSqueeze3 = squeeze3 <= 0;
# 1 Hr
def average4 = MovingAverage(average, close(period = AP4), length);
def shiftMid4 = nK * MovingAverage(average, TrueRange(high(period = AP4), close(period = AP4), low(period = AP4)), length);
def KeltMid4 = average4 + shiftMid4;
def squeeze4 = reference BollingerBands(price = close(period = AP4), length = length, numDevDn = nBB).upperband - KeltMid4;
def inSqueeze4 = squeeze4 <= 0;
# 2 Hr
def average5 = MovingAverage(average, close(period = AP5), length);
def shiftMid5 = nK * MovingAverage(average, TrueRange(high(period = AP5), close(period = AP5), low(period = AP5)), length);
def KeltMid5 = average5 + shiftMid5;
def squeeze5 = reference BollingerBands(price = close(period = AP5), length = length, numDevDn = nBB).upperband - KeltMid5;
def inSqueeze5 = squeeze5 <= 0;
# 4 Hr
def average6 = MovingAverage(average, close(period = AP6), length);
def shiftMid6 = nK * MovingAverage(average, TrueRange(high(period = AP6), close(period = AP6), low(period = AP6)), length);
def KeltMid6 = average6 + shiftMid6;
def squeeze6 = reference BollingerBands(price = close(period = AP6), length = length, numDevDn = nBB).upperband - KeltMid6;
def inSqueeze6 = squeeze6 <= 0;
# DAY
def average7 = MovingAverage(average, close(period = AP7), length);
def shiftMid7 = nK * MovingAverage(average, TrueRange(high(period = AP7), close(period = AP7), low(period = AP7)), length);
def KeltMid7 = average7 + shiftMid7;
def squeeze7 = reference BollingerBands(price = close(period = AP7), length = length, numDevDn = nBB).upperband - KeltMid7;
def inSqueeze7 = squeeze7 <= 0;
# Calculate direction after squeeze (only fires once when squeeze ends)
def direction1 = if !inSqueeze1 and inSqueeze1[1] then if close > close[1] then 1 else -1 else 0;
def direction2 = if !inSqueeze2 and inSqueeze2[1] then if close(period = AP2) > close(period = AP2)[1] then 1 else -1 else 0;
def direction3 = if !inSqueeze3 and inSqueeze3[1] then if close(period = AP3) > close(period = AP3)[1] then 1 else -1 else 0;
def direction4 = if !inSqueeze4 and inSqueeze4[1] then if close(period = AP4) > close(period = AP4)[1] then 1 else -1 else 0;
def direction5 = if !inSqueeze5 and inSqueeze5[1] then if close(period = AP5) > close(period = AP5)[1] then 1 else -1 else 0;
def direction6 = if !inSqueeze6 and inSqueeze6[1] then if close(period = AP6) > close(period = AP6)[1] then 1 else -1 else 0;
def direction7 = if !inSqueeze7 and inSqueeze7[1] then if close(period = AP7) > close(period = AP7)[1] then 1 else -1 else 0;
DefineGlobalColor("Bull", Color.Cyan);
DefineGlobalColor("Bear", Color.Magenta);
DefineGlobalColor("Up", Color.Cyan);
DefineGlobalColor("Down", Color.Magenta);
# ===== Timeframe anchor highs/lows (flat across each TF candle duration) =====
def lo1 = low;
def hi1 = high;
def lo2 = low(period = AP2);
def hi2 = high(period = AP2);
def lo3 = low(period = AP3);
def hi3 = high(period = AP3);
def lo4 = low(period = AP4);
def hi4 = high(period = AP4);
def lo5 = low(period = AP5);
def hi5 = high(period = AP5);
def lo6 = low(period = AP6);
def hi6 = high(period = AP6);
def lo7 = low(period = AP7);
def hi7 = high(period = AP7);
# ===== Bubbles (gated by bubbleOK) =====
AddChartBubble(bubbleOK and direction1 != 0 and inSqueeze1[1], if direction1 > 0 then low else high, "🔥5",
               if direction1 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction1 <= 0);
AddChartBubble(bubbleOK and direction2 != 0 and inSqueeze2[1], if direction2 > 0 then lo2 else hi2, "🔥15",
               if direction2 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction2 <= 0);
AddChartBubble(bubbleOK and direction3 != 0 and inSqueeze3[1], if direction3 > 0 then lo3 else hi3, "🔥30",
               if direction3 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction3 <= 0);
AddChartBubble(bubbleOK and direction4 != 0 and inSqueeze4[1], if direction4 > 0 then lo4 else hi4, "🔥1h",
               if direction4 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction4 <= 0);
AddChartBubble(bubbleOK and direction5 != 0 and inSqueeze5[1], if direction5 > 0 then lo5 else hi5, "🔥2h",
               if direction5 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction5 <= 0);
AddChartBubble(bubbleOK and direction6 != 0 and inSqueeze6[1], if direction6 > 0 then lo6 else hi6, "🔥4h",
               if direction6 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction6 <= 0);
AddChartBubble(bubbleOK and direction7 != 0 and inSqueeze7[1], if direction7 > 0 then lo7 else hi7, "🔥D",
               if direction7 > 0 then GlobalColor("Bull") else GlobalColor("Bear"), direction7 <= 0);
# ===== Clouds (ONE-SIDED + TF-ANCHORED; gated by cloudOK) =====
def squeezeEnded1 = direction1 != 0;
def squeezeEnded2 = direction2 != 0;
def squeezeEnded3 = direction3 != 0;
def squeezeEnded4 = direction4 != 0;
def squeezeEnded5 = direction5 != 0;
def squeezeEnded6 = direction6 != 0;
def squeezeEnded7 = direction7 != 0;
# 5m/current
AddCloud(if cloudOK and showCloud1 and squeezeEnded1 and direction1 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud1 and squeezeEnded1 and direction1 > 0 then lo1 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud1 and squeezeEnded1 and direction1 < 0 then hi1 else Double.NaN,
         if cloudOK and showCloud1 and squeezeEnded1 and direction1 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 15m
AddCloud(if cloudOK and showCloud2 and squeezeEnded2 and direction2 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud2 and squeezeEnded2 and direction2 > 0 then lo2 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud2 and squeezeEnded2 and direction2 < 0 then hi2 else Double.NaN,
         if cloudOK and showCloud2 and squeezeEnded2 and direction2 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 30m
AddCloud(if cloudOK and showCloud3 and squeezeEnded3 and direction3 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud3 and squeezeEnded3 and direction3 > 0 then lo3 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud3 and squeezeEnded3 and direction3 < 0 then hi3 else Double.NaN,
         if cloudOK and showCloud3 and squeezeEnded3 and direction3 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 1h
AddCloud(if cloudOK and showCloud4 and squeezeEnded4 and direction4 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud4 and squeezeEnded4 and direction4 > 0 then lo4 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud4 and squeezeEnded4 and direction4 < 0 then hi4 else Double.NaN,
         if cloudOK and showCloud4 and squeezeEnded4 and direction4 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 2h
AddCloud(if cloudOK and showCloud5 and squeezeEnded5 and direction5 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud5 and squeezeEnded5 and direction5 > 0 then lo5 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud5 and squeezeEnded5 and direction5 < 0 then hi5 else Double.NaN,
         if cloudOK and showCloud5 and squeezeEnded5 and direction5 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 4h
AddCloud(if cloudOK and showCloud6 and squeezeEnded6 and direction6 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud6 and squeezeEnded6 and direction6 > 0 then lo6 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud6 and squeezeEnded6 and direction6 < 0 then hi6 else Double.NaN,
         if cloudOK and showCloud6 and squeezeEnded6 and direction6 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# Daily
AddCloud(if cloudOK and showCloud7 and squeezeEnded7 and direction7 > 0 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud7 and squeezeEnded7 and direction7 > 0 then lo7 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud7 and squeezeEnded7 and direction7 < 0 then hi7 else Double.NaN,
         if cloudOK and showCloud7 and squeezeEnded7 and direction7 < 0 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));