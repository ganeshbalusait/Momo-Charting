#Author: @clarkalphas | Credit: Trading Alphas (c) 2025 | Website: https://www.tradingalphas.com/
input EMA1 = 9;
input EMA2 = 21;
input EMA3 = 50;
def SMA1 = 50;
def EMA5 = 200;
def SMA5 = 200;
def agg = AggregationPeriod.FIFTEEN_MIN;
def agg2 = AggregationPeriod.THIRTY_MIN;
input showMALines = no;
input showMACloud = Yes;
input showTrendBubbles = yes;
input show0mArrows = no;
input show1mArrows = yes;
input show5mArrows = yes;
input show15mArrows = yes;
input show1mBubbles = no;
input show5mBubbles = yes;
input show15mBubbles = yes;
input show1mCP = no;
input show5mCP = yes;
input show15mCP = yes;
DefineGlobalColor("35C", CreateColor(0, 178, 178));
DefineGlobalColor("35P", CreateColor(153, 60, 153));
DefineGlobalColor("C", CreateColor(0, 175, 175));
DefineGlobalColor("P", CreateColor(190, 0, 190));
DefineGlobalColor("CALL", Color.CYAN);
DefineGlobalColor("PUT", Color.MAGENTA);
## EMA ##
#3&5
def EMA3m = MovingAverage(AverageType.EXPONENTIAL, close, 2);
def EMA5m = MovingAverage(AverageType.EXPONENTIAL, close, 3);
#4&8
def EMA4 = MovingAverage(AverageType.EXPONENTIAL, close, 4);
def EMA8 = MovingAverage(AverageType.EXPONENTIAL, close, 8);
#20
def EMA20 = MovingAverage(AverageType.EXPONENTIAL, close, 20);
#9&21
plot EMA9 = MovingAverage(AverageType.EXPONENTIAL, close, EMA1);
EMA9.SetDefaultColor(Color.MAGENTA);
EMA9.SetPaintingStrategy(PaintingStrategy.LINE);
EMA9.SetLineWeight(1);
plot EMA21 = MovingAverage(AverageType.EXPONENTIAL, close, EMA2);
EMA21.SetDefaultColor(Color.YELLOW);
EMA21.SetPaintingStrategy(PaintingStrategy.LINE);
EMA21.SetLineWeight(1);
#50
plot EMA50 = MovingAverage(AverageType.EXPONENTIAL, close, EMA3);
EMA50.SetDefaultColor(Color.CYAN);
EMA50.SetPaintingStrategy(PaintingStrategy.LINE);
EMA50.SetLineWeight(1);
def SMA50 = MovingAverage(AverageType.SIMPLE, close, SMA1);
    #SMA50.SetDefaultColor(Color.Blue);
    #SMA50.SetPaintingStrategy(PaintingStrategy.line);
    #SMA50.SetLineWeight(1);
#200
def EMA200 = MovingAverage(AverageType.EXPONENTIAL, close, EMA5);
    #EMA200.SetDefaultColor(Color.Light_Green);
    #EMA200.SetPaintingStrategy(PaintingStrategy.line);
    #EMA200.SetLineWeight(1);
plot SMA200 = MovingAverage(AverageType.SIMPLE, close, SMA5);
SMA200.SetDefaultColor(Color.DARK_GREEN);
SMA200.SetPaintingStrategy(PaintingStrategy.LINE);
SMA200.SetLineWeight(2);
EMA9.SetHiding(!showMALines);
EMA21.SetHiding(!showMALines);
EMA50.SetHiding(!showMALines);
SMA200.SetHiding(!showMALines);
## EMA CLOUDS ## 
DefineGlobalColor("Bull9", Color.CYAN);
DefineGlobalColor("Bear9", Color.MAGENTA);
DefineGlobalColor("Bull21", Color.CYAN);
DefineGlobalColor("Bear21", Color.MAGENTA);
DefineGlobalColor("48C", CreateColor(169, 255, 0));
DefineGlobalColor("48P", CreateColor(255, 0, 106));
AddCloud(if showMACloud then EMA21 else Double.NaN, EMA50, GlobalColor("Bull21"), GlobalColor("Bear21"));
AddCloud(if showMACloud then EMA9 else Double.NaN, EMA21, GlobalColor("Bull9"), GlobalColor("Bear9"));
## MTF EMA CROSSES ##
#CURRENT
plot UP48 = if EMA4 crosses above EMA8 then low else Double.NaN;
UP48.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48.SetLineWeight(5);
UP48.SetDefaultColor(GlobalColor("48C"));
plot DN48 = if EMA4 crosses below EMA8
               then high
               else Double.NaN;
DN48.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48.SetLineWeight(5);
DN48.SetDefaultColor(GlobalColor("48P"));
plot UP35 = if EMA3m crosses above EMA5m then low else Double.NaN;
UP35.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP35.SetLineWeight(2);
UP35.SetDefaultColor(GlobalColor("35C"));
plot DN35 = if EMA3m crosses below EMA5m
               then high
               else Double.NaN;
DN35.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN35.SetLineWeight(2);
DN35.SetDefaultColor(GlobalColor("35P"));
#CURRENT
plot UP920 = if EMA9 crosses above EMA20 then low else Double.NaN;
UP920.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP920.SetLineWeight(3);
UP920.SetDefaultColor(Color.CYAN);
plot DN920 = if EMA9 crosses below EMA20
               then high
               else Double.NaN;
DN920.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN920.SetLineWeight(3);
DN920.SetDefaultColor(Color.MAGENTA);
#AGG-15M
def EMA9AGG = MovingAverage(AverageType.EXPONENTIAL, close(period = agg), 9);
def EMA20AGG = MovingAverage(AverageType.EXPONENTIAL, close(period = agg), 20);
plot UP920AGG = if EMA9AGG crosses above EMA20AGG then low else Double.NaN;
UP920AGG.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP920AGG.SetLineWeight(3);
UP920AGG.SetDefaultColor(Color.CYAN);
plot DN920AGG = if EMA9AGG crosses below EMA20AGG then high else Double.NaN;
DN920AGG.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN920AGG.SetLineWeight(3);
DN920AGG.SetDefaultColor(Color.MAGENTA);
## CLOUD TREND MTF ##
#CURRENT
def EXU = MovAvgExponential()."AvgExp" is greater than or equal to MovAvgExponential("length" = 20)."AvgExp";
def EXD = MovAvgExponential()."AvgExp" is less than or equal to MovAvgExponential("length" = 20)."AvgExp";
#AGG-15M
def MTU = ExpAverage(close(period = agg), EMA1) is greater than or equal to ExpAverage(close(period = agg), 20);
def MTD = ExpAverage(close(period = agg), EMA1) is less than or equal to ExpAverage(close(period = agg), 20);
#AGG2-30M
def MTU2 = ExpAverage(close(period = agg2), EMA1) is greater than or equal to ExpAverage(close(period = agg2), 20);
def MTD2 = ExpAverage(close(period = agg2), EMA1) is less than or equal to ExpAverage(close(period = agg2), 20);
UP35.SetHiding(!show0mArrows);
DN35.SetHiding(!show0mArrows);
UP48.SetHiding(!show1mArrows);
DN48.SetHiding(!show1mArrows);
UP920.SetHiding(!show5mArrows);
DN920.SetHiding(!show5mArrows);
UP920AGG.SetHiding(!show15mArrows);
DN920AGG.SetHiding(!show15mArrows);
## MTF BUBBLE ##
#CURRENT-1m
#AddChartBubble(showTrendBubbles and show1mBubbles and show1mCP and UP48 and EXD, low, "C1", GlobalColor("C"), no);
#AddChartBubble(showTrendBubbles and show1mBubbles and show1mCP and DN48 and EXU, high, "P1", GlobalColor("P"), yes);
AddChartBubble(showTrendBubbles and show1mBubbles and UP48 and EXU, low, "CALL1", GlobalColor("CALL"), no);
AddChartBubble(showTrendBubbles and show1mBubbles and DN48 and EXD, high, "PUT1", GlobalColor("PUT"), yes);
#AddChartBubble(showTrendBubbles and show1mBubbles and show1mCP and UP48 and EXD and LowK and LowD and O21 and SqueezeHigh, low, "C1", GlobalColor("C"), no);
#AddChartBubble(showTrendBubbles and show1mBubbles and show1mCP and DN48 and EXU and HighK and HighK and U21 and SqueezeHigh, high, "P1", GlobalColor("P"), yes);
#AddChartBubble(showTrendBubbles and show1mBubbles and UP48 and EXU and LowK and LowD and SqueezeHigh, low, "CALL1", GlobalColor("CALL"), no);
#AddChartBubble(showTrendBubbles and show1mBubbles and DN48 and EXD and HighK and HighD and SqueezeHigh, high, "PUT1", GlobalColor("PUT"), yes);
#CURRENT-5m
AddChartBubble(showTrendBubbles and show5mBubbles and show5mCP and UP920 and MTD, low, "C5", GlobalColor("C"), no);
AddChartBubble(showTrendBubbles and show5mBubbles and show5mCP and DN920 and MTU, high, "P5", GlobalColor("P"), yes);
AddChartBubble(showTrendBubbles and show5mBubbles and UP920 and MTU, low, "CALL5", GlobalColor("CALL"), no);
AddChartBubble(showTrendBubbles and show5mBubbles and DN920 and MTD, high, "PUT5", GlobalColor("PUT"), yes);
#AGG-15m
AddChartBubble(showTrendBubbles and show15mBubbles and show15mCP and UP920AGG and MTD2, low, "C15", GlobalColor("C"), no);
AddChartBubble(showTrendBubbles and show15mBubbles and show15mCP and DN920AGG and MTU2, high, "P15", GlobalColor("P"), yes);
AddChartBubble(showTrendBubbles and show15mBubbles and UP920AGG and MTU2, low, "CALL15",  GlobalColor("CALL"), no);
AddChartBubble(showTrendBubbles and show15mBubbles and DN920AGG and MTD2, high, "PUT15", GlobalColor("PUT"), yes);