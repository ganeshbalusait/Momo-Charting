# shared_MTF_Squeeze410 - pasted by Ganesh 2026-10-03 (with CRDO 5m TOS screenshots).
# Settings in his TOS: AP2..AP8 = 15m, 30m, 1h, 2h, 4h, Day, Week; average SIMPLE;
# every plot: Show plot, Show bubble, Show title; Draw as SQUARED_HISTOGRAM; width 5.
# Script body (verbatim structure, condensed per timeframe - the 8 blocks are identical
# apart from the period):
#Author: @clarkalphas | Credit: Trading Alphas (c) 2025 | Website: https://www.tradingalphas.com/
declare lower;
input AP2 = AggregationPeriod.FIFTEEN_MIN;
input AP3 = AggregationPeriod.THIRTY_MIN;
input AP4 = AggregationPeriod.HOUR;
input AP5 = AggregationPeriod.TWO_HOURS;
input AP6 = AggregationPeriod.FOUR_HOURS;
input AP7 = AggregationPeriod.DAY;
input AP8 = AggregationPeriod.WEEK;
input average = AverageType.SIMPLE;
def length = 20;
def nK_High = 1.0;
def nK = 1.5;
def nK_Low = 2.0;
def nBB = 2.0;
def alertLine = 1.0;

# per timeframe N with price P = close / close(period = APn):
#   averageN  = MovingAverage(average, P, length)
#   KeltLowN  = averageN + nK_Low  * MovingAverage(average, TrueRange(high, close, low), length)
#   KeltMidN  = averageN + nK      * MovingAverage(average, TrueRange(...), length)
#   KeltHighN = averageN + nK_High * MovingAverage(average, TrueRange(...), length)
#   BolKelDelta_{Low,Mid,High}N = BollingerBands(P, num_dev_up = nBB, length).upperband - Kelt{Low,Mid,High}N
#   squeezeDotsN = TTM_Squeeze(P, length, nK, nBB, alertLine).SqueezeAlert

plot Squeeze = if squeezeDots1 == 0 then squeezeDots1 + .5 else .5;
plot Squeeze2 = if squeezeDots2 == 0 then squeezeDots2 + 1 else 1;
plot Squeeze3 = if squeezeDots3 == 0 then squeezeDots3 + 1.5 else 1.5;
plot Squeeze4 = if squeezeDots4 == 0 then squeezeDots4 + 2 else 2;
plot Squeeze5 = if squeezeDots5 == 0 then squeezeDots5 + 2.5 else 2.5;
plot Squeeze6 = if squeezeDots6 == 0 then squeezeDots6 + 3 else 3;
plot Squeeze7 = if squeezeDots7 == 0 then squeezeDots7 + 3.5 else 3.5;
plot Squeeze8 = if squeezeDots8 == 0 then squeezeDots7 + 4 else 4;   # (script uses squeezeDots7 here)

# each SquezeN: SQUARED_HISTOGRAM, weight 5, colour:
#   if BolKelDelta_HighN <= 0 then SqueezeHigh else
#   if BolKelDelta_MidN  <= 0 then SqueezeMid  else
#   if BolKelDelta_LowN  <= 0 then SqueezeLow  else noSqueeze
# labels "5M","15M","30M","1H🔄","2H🔄","4H","D","W": same tests, Low -> SqueezeBlue, none -> Transparent

DefineGlobalColor("NoSqueeze", Color.BLACK);
DefineGlobalColor("SqueezeBlue", CreateColor(127,108,0));
DefineGlobalColor("SqueezeLow", CreateColor(127,108,0));
DefineGlobalColor("SqueezeMid", Color.ORANGE);      # TOS ORANGE = (255, 200, 0), measured from his screenshot
DefineGlobalColor("SqueezeHigh", Color.WHITE);
DefineGlobalColor("Transparent", Color.BLACK);
