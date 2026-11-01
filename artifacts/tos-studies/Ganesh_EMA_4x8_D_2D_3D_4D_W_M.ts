# Higher Timeframe CALL + PUT Signals
# D / 2D / 3D / 4D / W / M
# EMA 4 crosses EMA 8 + next higher timeframe confirmation
def aggD  = AggregationPeriod.DAY;
def agg2D = AggregationPeriod.TWO_DAYS;
def agg3D = AggregationPeriod.THREE_DAYS;
def agg4D = AggregationPeriod.FOUR_DAYS;
def aggW  = AggregationPeriod.WEEK;
def aggM  = AggregationPeriod.MONTH;
input showTrendBubbles = yes;
input showDArrows  = no;
input show2DArrows = no;
input show3DArrows = no;
input show4DArrows = no;
input showWArrows  = no;
input showMArrows  = no;
input showDBubbles  = yes;
input show2DBubbles = yes;
input show3DBubbles = yes;
input show4DBubbles = yes;
input showWBubbles  = yes;
input showMBubbles  = yes;
DefineGlobalColor("CALL", CreateColor(169, 255, 0));
DefineGlobalColor("PUT", CreateColor(255, 0, 106));
DefineGlobalColor("C", CreateColor(169, 255, 0));
DefineGlobalColor("P", CreateColor(255, 0, 106));
def bubbleOK = showTrendBubbles;
# =========================
# EMA 4 / EMA 8
# =========================
# Daily
def EMA4D = MovingAverage(AverageType.EXPONENTIAL, close(period = aggD), 4);
def EMA8D = MovingAverage(AverageType.EXPONENTIAL, close(period = aggD), 8);
plot UP48D =
    if EMA4D crosses above EMA8D
    then low
    else Double.NaN;
plot DN48D =
    if EMA4D crosses below EMA8D
    then high
    else Double.NaN;
UP48D.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48D.SetLineWeight(5);
UP48D.SetDefaultColor(GlobalColor("CALL"));
DN48D.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48D.SetLineWeight(5);
DN48D.SetDefaultColor(GlobalColor("PUT"));
# 2D
def EMA4_2D = MovingAverage(AverageType.EXPONENTIAL, close(period = agg2D), 4);
def EMA8_2D = MovingAverage(AverageType.EXPONENTIAL, close(period = agg2D), 8);
plot UP48_2D =
    if EMA4_2D crosses above EMA8_2D
    then low
    else Double.NaN;
plot DN48_2D =
    if EMA4_2D crosses below EMA8_2D
    then high
    else Double.NaN;
UP48_2D.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48_2D.SetLineWeight(5);
UP48_2D.SetDefaultColor(GlobalColor("CALL"));
DN48_2D.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48_2D.SetLineWeight(5);
DN48_2D.SetDefaultColor(GlobalColor("PUT"));
# 3D
def EMA4_3D = MovingAverage(AverageType.EXPONENTIAL, close(period = agg3D), 4);
def EMA8_3D = MovingAverage(AverageType.EXPONENTIAL, close(period = agg3D), 8);
plot UP48_3D =
    if EMA4_3D crosses above EMA8_3D
    then low
    else Double.NaN;
plot DN48_3D =
    if EMA4_3D crosses below EMA8_3D
    then high
    else Double.NaN;
UP48_3D.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48_3D.SetLineWeight(5);
UP48_3D.SetDefaultColor(GlobalColor("CALL"));
DN48_3D.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48_3D.SetLineWeight(5);
DN48_3D.SetDefaultColor(GlobalColor("PUT"));
# 4D
def EMA4_4D = MovingAverage(AverageType.EXPONENTIAL, close(period = agg4D), 4);
def EMA8_4D = MovingAverage(AverageType.EXPONENTIAL, close(period = agg4D), 8);
plot UP48_4D =
    if EMA4_4D crosses above EMA8_4D
    then low
    else Double.NaN;
plot DN48_4D =
    if EMA4_4D crosses below EMA8_4D
    then high
    else Double.NaN;
UP48_4D.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48_4D.SetLineWeight(5);
UP48_4D.SetDefaultColor(GlobalColor("CALL"));
DN48_4D.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48_4D.SetLineWeight(5);
DN48_4D.SetDefaultColor(GlobalColor("PUT"));
# Weekly
def EMA4W = MovingAverage(AverageType.EXPONENTIAL, close(period = aggW), 4);
def EMA8W = MovingAverage(AverageType.EXPONENTIAL, close(period = aggW), 8);
plot UP48W =
    if EMA4W crosses above EMA8W
    then low
    else Double.NaN;
plot DN48W =
    if EMA4W crosses below EMA8W
    then high
    else Double.NaN;
UP48W.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48W.SetLineWeight(5);
UP48W.SetDefaultColor(GlobalColor("CALL"));
DN48W.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48W.SetLineWeight(5);
DN48W.SetDefaultColor(GlobalColor("PUT"));
# Monthly
def EMA4M = MovingAverage(AverageType.EXPONENTIAL, close(period = aggM), 4);
def EMA8M = MovingAverage(AverageType.EXPONENTIAL, close(period = aggM), 8);
plot UP48M =
    if EMA4M crosses above EMA8M
    then low
    else Double.NaN;
plot DN48M =
    if EMA4M crosses below EMA8M
    then high
    else Double.NaN;
UP48M.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
UP48M.SetLineWeight(5);
UP48M.SetDefaultColor(GlobalColor("CALL"));
DN48M.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
DN48M.SetLineWeight(5);
DN48M.SetDefaultColor(GlobalColor("PUT"));
# =========================
# Higher Timeframe Confirmation
# =========================
def MTU2D = EMA4_2D >= EMA8_2D;
def MTD2D = EMA4_2D <= EMA8_2D;
def MTU3D = EMA4_3D >= EMA8_3D;
def MTD3D = EMA4_3D <= EMA8_3D;
def MTU4D = EMA4_4D >= EMA8_4D;
def MTD4D = EMA4_4D <= EMA8_4D;
def MTUW  = EMA4W >= EMA8W;
def MTDW  = EMA4W <= EMA8W;
def MTUM  = EMA4M >= EMA8M;
def MTDM  = EMA4M <= EMA8M;
# =========================
# Hide / Show Arrows
# =========================
UP48D.SetHiding(!showDArrows);
DN48D.SetHiding(!showDArrows);
UP48_2D.SetHiding(!show2DArrows);
DN48_2D.SetHiding(!show2DArrows);
UP48_3D.SetHiding(!show3DArrows);
DN48_3D.SetHiding(!show3DArrows);
UP48_4D.SetHiding(!show4DArrows);
DN48_4D.SetHiding(!show4DArrows);
UP48W.SetHiding(!showWArrows);
DN48W.SetHiding(!showWArrows);
UP48M.SetHiding(!showMArrows);
DN48M.SetHiding(!showMArrows);
# =========================
# CALL + PUT Bubbles
# =========================
# Daily
AddChartBubble(
    bubbleOK and showDBubbles and UP48D and MTU2D,
    low,
    "CALLD",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    bubbleOK and showDBubbles and DN48D and MTD2D,
    high,
    "PUTD",
    GlobalColor("PUT"),
    yes
);
# 2D
AddChartBubble(
    bubbleOK and show2DBubbles and UP48_2D and MTU3D,
    low,
    "CALL2D",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    bubbleOK and show2DBubbles and DN48_2D and MTD3D,
    high,
    "PUT2D",
    GlobalColor("PUT"),
    yes
);
# 3D
AddChartBubble(
    bubbleOK and show3DBubbles and UP48_3D and MTU4D,
    low,
    "CALL3D",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    bubbleOK and show3DBubbles and DN48_3D and MTD4D,
    high,
    "PUT3D",
    GlobalColor("PUT"),
    yes
);
# 4D
AddChartBubble(
    bubbleOK and show4DBubbles and UP48_4D and MTUW,
    low,
    "CALL4D",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    bubbleOK and show4DBubbles and DN48_4D and MTDW,
    high,
    "PUT4D",
    GlobalColor("PUT"),
    yes
);
# Weekly
AddChartBubble(
    bubbleOK and showWBubbles and UP48W and MTUM,
    low,
    "CALLW",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    bubbleOK and showWBubbles and DN48W and MTDM,
    high,
    "PUTW",
    GlobalColor("PUT"),
    yes
);
# Monthly
AddChartBubble(
    bubbleOK and showMBubbles and UP48M,
    low,
    "CALLM",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    bubbleOK and showMBubbles and DN48M,
    high,
    "PUTM",
    GlobalColor("PUT"),
    yes
);