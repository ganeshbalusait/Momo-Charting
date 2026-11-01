#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/
input AP2 = AggregationPeriod.FIFTEEN_MIN;
##############################
def price = close(period = AP2);
def displace = 0;
input length = 20;
input DevDn = -2.0;
input DevUp = 2.0;
def kHigh = 1.0;
def kMid  = 1.5;
def kLow  = 2.0;
def averageType = AverageType.SIMPLE;
def trueRangeAverageType = AverageType.SIMPLE;
input showBollingerLines = No;
input showKeltnerChannels = No;
input showCloudSqueezeHigh = Yes;
input showCloudSqueeze     = Yes;
input showCloudSqueezeLow  = Yes;
# ==========================
# LAST N RTH SESSIONS FILTER (Extended-hours safe)
# ==========================
input limitToLastNSessions = yes;
input sessionsBack = 1;          # 1=today RTH only, 2=today+prior RTH, etc.
input rthStartTime = 0500;
input rthEndTime   = 1600;
input extendIntoExpansion = no;
def agg = GetAggregationPeriod();
def isIntraday = agg < AggregationPeriod.DAY;
def inRTH = SecondsFromTime(rthStartTime) >= 0 and SecondsTillTime(rthEndTime) > 0;
def newRTHSession = inRTH and !inRTH[1];
def ymd = GetYYYYMMDD();
def newDay = ymd <> ymd[1];
# Count sessions for intraday charts; count days for daily+ charts
def idx =
    CompoundValue(
        1,
        idx[1] + (if isIntraday then (if newRTHSession then 1 else 0) else (if newDay then 1 else 0)),
        0
    );
def lastIdx = HighestAll(idx);
def showRecent =
    if !limitToLastNSessions then 1
    else idx >= (lastIdx - (sessionsBack - 1));
def lastDataBar = HighestAll(if !IsNaN(close) then BarNumber() else 0);
def inExpansion = BarNumber() > lastDataBar;
def showOK = showRecent or (extendIntoExpansion and inExpansion);
# ==========================
# BOLLINGER BAND
# ==========================
def sDev = StDev(data = price[-displace], length = length);
def MidLine = MovingAverage(averageType, data = price[-displace], length = length);
plot LowerBand = MidLine + DevDn * sDev;
LowerBand.SetDefaultColor(Color.BLUE);
plot UpperBand = MidLine + DevUp * sDev;
UpperBand.SetDefaultColor(Color.BLUE);
# ==========================
# KELTNER CHANNELS
# ==========================
def average = MovingAverage(averageType, price, length);
# MID SQUEEZE
def shift = kMid * MovingAverage(trueRangeAverageType,
                 TrueRange(high(period = AP2), close(period = AP2), low(period = AP2)), length);
plot UpperKeltMid = average[-displace] + shift[-displace];
UpperKeltMid.SetDefaultColor(Color.ORANGE);
plot LowerKeltMid = average[-displace] - shift[-displace];
LowerKeltMid.SetDefaultColor(Color.ORANGE);
# HIGH SQUEEZE
def shiftH = kHigh * MovingAverage(trueRangeAverageType,
                 TrueRange(high(period = AP2), close(period = AP2), low(period = AP2)), length);
plot UpperKeltHigh = average[-displace] + shiftH[-displace];
UpperKeltHigh.SetDefaultColor(Color.WHITE);
plot LowerKeltHigh = average[-displace] - shiftH[-displace];
LowerKeltHigh.SetDefaultColor(Color.WHITE);
# LOW SQUEEZE
def shiftL = kLow * MovingAverage(trueRangeAverageType,
                 TrueRange(high(period = AP2), close(period = AP2), low(period = AP2)), length);
plot UpperKeltLow = average[-displace] + shiftL[-displace];
UpperKeltLow.SetDefaultColor(Color.VIOLET);
plot LowerKeltLow = average[-displace] - shiftL[-displace];
LowerKeltLow.SetDefaultColor(Color.VIOLET);
# ==========================
# VISIBILITY TOGGLES
# ==========================
LowerBand.SetHiding(!showBollingerLines);
UpperBand.SetHiding(!showBollingerLines);
UpperKeltMid.SetHiding(!showKeltnerChannels);
LowerKeltMid.SetHiding(!showKeltnerChannels);
UpperKeltHigh.SetHiding(!showKeltnerChannels);
LowerKeltHigh.SetHiding(!showKeltnerChannels);
UpperKeltLow.SetHiding(!showKeltnerChannels);
LowerKeltLow.SetHiding(!showKeltnerChannels);
# ==========================
# COLORS
# ==========================
DefineGlobalColor("Transparent", Color.CURRENT);
DefineGlobalColor("White", Color.WHITE);
DefineGlobalColor("Gold", CreateColor(255,218,0));
DefineGlobalColor("Blue", CreateColor(255,218,0));
# ==========================
# CLOUD SQUEEZE (GATED TO LAST N)
# ==========================
AddCloud(
    if showOK and showCloudSqueezeLow then UpperKeltLow else Double.NaN,
    if showOK then UpperBand else Double.NaN,
    GlobalColor("Blue"),
    GlobalColor("Transparent")
);
AddCloud(
    if showOK and showCloudSqueezeLow then LowerBand else Double.NaN,
    if showOK then LowerKeltLow else Double.NaN,
    GlobalColor("Blue"),
    GlobalColor("Transparent")
);
AddCloud(
    if showOK and showCloudSqueeze then UpperKeltMid else Double.NaN,
    if showOK then UpperBand else Double.NaN,
    GlobalColor("Gold"),
    GlobalColor("Transparent")
);
AddCloud(
    if showOK and showCloudSqueeze then LowerBand else Double.NaN,
    if showOK then LowerKeltMid else Double.NaN,
    GlobalColor("Gold"),
    GlobalColor("Transparent")
);
AddCloud(
    if showOK and showCloudSqueezeHigh then UpperKeltHigh else Double.NaN,
    if showOK then UpperBand else Double.NaN,
    GlobalColor("White"),
    GlobalColor("Transparent")
);
AddCloud(
    if showOK and showCloudSqueezeHigh then LowerBand else Double.NaN,
    if showOK then LowerKeltHigh else Double.NaN,
    GlobalColor("White"),
    GlobalColor("Transparent")
);