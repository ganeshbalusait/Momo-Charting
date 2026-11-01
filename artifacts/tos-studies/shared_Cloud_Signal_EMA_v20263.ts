#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/
input showClouds = yes;
# ===== Per-timeframe cloud toggles =====
input showCloud_Current = yes;
input showCloud_15m     = yes;
input showCloud_30m     = yes;
input showCloud_1hr     = yes;
input showCloud_2hr     = yes;
input showCloud_4hr     = yes;
# ===== Limit CLOUDS to last N RTH sessions (works with Extended Hours ON) =====
input limitCloudsToLastNSessions = yes;
input sessionsBack = 1;   # 1 = today RTH only, 2 = today + prior RTH, etc.
input rthStartTime = 0500;
input rthEndTime   = 1600;
def inRTH = SecondsFromTime(rthStartTime) >= 0 and SecondsTillTime(rthEndTime) > 0;
def newRTHSession = inRTH and !inRTH[1];
def sessIndex = CompoundValue(1, sessIndex[1] + (if newRTHSession then 1 else 0), 0);
def lastSessIndex = HighestAll(sessIndex);
# Includes overnight bars belonging to those sessions
def showRecent = if limitCloudsToLastNSessions
                 then sessIndex >= (lastSessIndex - (sessionsBack - 1))
                 else 1;
# If you ONLY want clouds during RTH (not overnight), use this instead:
# def showRecent = if limitCloudsToLastNSessions
#                  then inRTH and sessIndex >= (lastSessIndex - (sessionsBack - 1))
#                  else 1;
def cloudOK = showClouds and showRecent;
# EMA definitions (current chart aggregation)
def EMA9 = ExpAverage(close, 9);
def EMA20 = ExpAverage(close, 20);
# Crossovers for the current timeframe
def crossAboveCurrent = EMA9 crosses above EMA20;
def crossBelowCurrent = EMA9 crosses below EMA20;
# 15 Min timeframe
def EMA9_15 = ExpAverage(close(period = AggregationPeriod.FIFTEEN_MIN), 9);
def EMA20_15 = ExpAverage(close(period = AggregationPeriod.FIFTEEN_MIN), 20);
def crossAbove_15 = EMA9_15 crosses above EMA20_15;
def crossBelow_15 = EMA9_15 crosses below EMA20_15;
# 30 Min timeframe
def EMA9_30 = ExpAverage(close(period = AggregationPeriod.THIRTY_MIN), 9);
def EMA20_30 = ExpAverage(close(period = AggregationPeriod.THIRTY_MIN), 20);
def crossAbove_30 = EMA9_30 crosses above EMA20_30;
def crossBelow_30 = EMA9_30 crosses below EMA20_30;
# 1 Hr timeframe
def EMA9_1hr = ExpAverage(close(period = AggregationPeriod.HOUR), 9);
def EMA20_1hr = ExpAverage(close(period = AggregationPeriod.HOUR), 20);
def crossAbove_1hr = EMA9_1hr crosses above EMA20_1hr;
def crossBelow_1hr = EMA9_1hr crosses below EMA20_1hr;
# 2 Hr timeframe
def EMA9_2hr = ExpAverage(close(period = AggregationPeriod.TWO_HOURS), 9);
def EMA20_2hr = ExpAverage(close(period = AggregationPeriod.TWO_HOURS), 20);
def crossAbove_2hr = EMA9_2hr crosses above EMA20_2hr;
def crossBelow_2hr = EMA9_2hr crosses below EMA20_2hr;
# 4 Hr timeframe
def EMA9_4hr = ExpAverage(close(period = AggregationPeriod.FOUR_HOURS), 9);
def EMA20_4hr = ExpAverage(close(period = AggregationPeriod.FOUR_HOURS), 20);
def crossAbove_4hr = EMA9_4hr crosses above EMA20_4hr;
def crossBelow_4hr = EMA9_4hr crosses below EMA20_4hr;
DefineGlobalColor("Up", Color.Cyan);
DefineGlobalColor("Down", Color.Magenta);
# ===== Timeframe anchor highs/lows (flat across each TF candle duration) =====
def loCur = low;
def hiCur = high;
def lo15 = low(period = AggregationPeriod.FIFTEEN_MIN);
def hi15 = high(period = AggregationPeriod.FIFTEEN_MIN);
def lo30 = low(period = AggregationPeriod.THIRTY_MIN);
def hi30 = high(period = AggregationPeriod.THIRTY_MIN);
def lo1h = low(period = AggregationPeriod.HOUR);
def hi1h = high(period = AggregationPeriod.HOUR);
def lo2h = low(period = AggregationPeriod.TWO_HOURS);
def hi2h = high(period = AggregationPeriod.TWO_HOURS);
def lo4h = low(period = AggregationPeriod.FOUR_HOURS);
def hi4h = high(period = AggregationPeriod.FOUR_HOURS);
# ===== One-Sided Clouds (anchored to each timeframe's candle) =====
# CrossUp: shade ABOVE TF-LOW (TF low -> +infinity)
# CrossDown: shade BELOW TF-HIGH (-infinity -> TF high)
# Current TF
AddCloud(if cloudOK and showCloud_Current and crossAboveCurrent then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud_Current and crossAboveCurrent then loCur else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud_Current and crossBelowCurrent then hiCur else Double.NaN,
         if cloudOK and showCloud_Current and crossBelowCurrent then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 15m
AddCloud(if cloudOK and showCloud_15m and crossAbove_15 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud_15m and crossAbove_15 then lo15 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud_15m and crossBelow_15 then hi15 else Double.NaN,
         if cloudOK and showCloud_15m and crossBelow_15 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 30m
AddCloud(if cloudOK and showCloud_30m and crossAbove_30 then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud_30m and crossAbove_30 then lo30 else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud_30m and crossBelow_30 then hi30 else Double.NaN,
         if cloudOK and showCloud_30m and crossBelow_30 then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 1hr
AddCloud(if cloudOK and showCloud_1hr and crossAbove_1hr then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud_1hr and crossAbove_1hr then lo1h else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud_1hr and crossBelow_1hr then hi1h else Double.NaN,
         if cloudOK and showCloud_1hr and crossBelow_1hr then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 2hr
AddCloud(if cloudOK and showCloud_2hr and crossAbove_2hr then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud_2hr and crossAbove_2hr then lo2h else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud_2hr and crossBelow_2hr then hi2h else Double.NaN,
         if cloudOK and showCloud_2hr and crossBelow_2hr then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));
# 4hr
AddCloud(if cloudOK and showCloud_4hr and crossAbove_4hr then Double.POSITIVE_INFINITY else Double.NaN,
         if cloudOK and showCloud_4hr and crossAbove_4hr then lo4h else Double.NaN,
         GlobalColor("Up"), GlobalColor("Up"));
AddCloud(if cloudOK and showCloud_4hr and crossBelow_4hr then hi4h else Double.NaN,
         if cloudOK and showCloud_4hr and crossBelow_4hr then Double.NEGATIVE_INFINITY else Double.NaN,
         GlobalColor("Down"), GlobalColor("Down"));