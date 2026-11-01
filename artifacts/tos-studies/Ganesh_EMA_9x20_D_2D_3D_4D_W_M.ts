# D / 2D / 3D / 4D / W / M EMA CALL + PUT Signals
# Watchlist match style
# Fixed: print only once on first candle of the day/session
input fastLength = 9;
input slowLength = 20;
input showCALLD  = yes;
input showCALL2D = yes;
input showCALL3D = yes;
input showCALL4D = yes;
input showCALLW  = yes;
input showCALLM  = yes;
input showPUTD  = yes;
input showPUT2D = yes;
input showPUT3D = yes;
input showPUT4D = yes;
input showPUTW  = yes;
input showPUTM  = yes;
input rthStartTime = 0500;
DefineGlobalColor("CALL", Color.CYAN);
DefineGlobalColor("PUT", Color.MAGENTA);
def aggD  = AggregationPeriod.DAY;
def agg2D = AggregationPeriod.TWO_DAYS;
def agg3D = AggregationPeriod.THREE_DAYS;
def agg4D = AggregationPeriod.FOUR_DAYS;
def aggW  = AggregationPeriod.WEEK;
def aggM  = AggregationPeriod.MONTH;
# First candle of your trading day/session
def firstBarToday =
    GetYYYYMMDD() != GetYYYYMMDD()[1];
# If you want first bar after 5 AM instead, replace above with this:
# def firstBarToday = SecondsFromTime(rthStartTime) >= 0 and SecondsFromTime(rthStartTime)[1] < 0;
def atr = Average(TrueRange(high, close, low), 14);
# =========================
# D
# =========================
def EMAFastD = ExpAverage(close(period = aggD), fastLength);
def EMASlowD = ExpAverage(close(period = aggD), slowLength);
def rawCALLD =
    EMAFastD crosses above EMASlowD within 1 bars;
def rawPUTD =
    EMAFastD crosses below EMASlowD within 1 bars;
AddChartBubble(
    showCALLD and firstBarToday and rawCALLD,
    low - atr * 0.2,
    "CALLD",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    showPUTD and firstBarToday and rawPUTD,
    high + atr * 0.2,
    "PUTD",
    GlobalColor("PUT"),
    yes
);
# =========================
# 2D
# =========================
def EMAFast2D = ExpAverage(close(period = agg2D), fastLength);
def EMASlow2D = ExpAverage(close(period = agg2D), slowLength);
def rawCALL2D =
    EMAFast2D crosses above EMASlow2D within 1 bars;
def rawPUT2D =
    EMAFast2D crosses below EMASlow2D within 1 bars;
AddChartBubble(
    showCALL2D and firstBarToday and rawCALL2D,
    low - atr * 0.6,
    "CALL2D",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    showPUT2D and firstBarToday and rawPUT2D,
    high + atr * 0.6,
    "PUT2D",
    GlobalColor("PUT"),
    yes
);
# =========================
# 3D
# =========================
def EMAFast3D = ExpAverage(close(period = agg3D), fastLength);
def EMASlow3D = ExpAverage(close(period = agg3D), slowLength);
def rawCALL3D =
    EMAFast3D crosses above EMASlow3D within 1 bars;
def rawPUT3D =
    EMAFast3D crosses below EMASlow3D within 1 bars;
AddChartBubble(
    showCALL3D and firstBarToday and rawCALL3D,
    low - atr * 1.0,
    "CALL3D",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    showPUT3D and firstBarToday and rawPUT3D,
    high + atr * 1.0,
    "PUT3D",
    GlobalColor("PUT"),
    yes
);
# =========================
# 4D
# =========================
def EMAFast4D = ExpAverage(close(period = agg4D), fastLength);
def EMASlow4D = ExpAverage(close(period = agg4D), slowLength);
def rawCALL4D =
    EMAFast4D crosses above EMASlow4D within 1 bars;
def rawPUT4D =
    EMAFast4D crosses below EMASlow4D within 1 bars;
AddChartBubble(
    showCALL4D and firstBarToday and rawCALL4D,
    low - atr * 1.4,
    "CALL4D",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    showPUT4D and firstBarToday and rawPUT4D,
    high + atr * 1.4,
    "PUT4D",
    GlobalColor("PUT"),
    yes
);
# =========================
# Weekly
# =========================
def EMAFastW = ExpAverage(close(period = aggW), fastLength);
def EMASlowW = ExpAverage(close(period = aggW), slowLength);
def rawCALLW =
    EMAFastW crosses above EMASlowW within 1 bars;
def rawPUTW =
    EMAFastW crosses below EMASlowW within 1 bars;
AddChartBubble(
    showCALLW and firstBarToday and rawCALLW,
    low - atr * 1.8,
    "CALLW",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    showPUTW and firstBarToday and rawPUTW,
    high + atr * 1.8,
    "PUTW",
    GlobalColor("PUT"),
    yes
);
# =========================
# Monthly
# =========================
def EMAFastM = ExpAverage(close(period = aggM), fastLength);
def EMASlowM = ExpAverage(close(period = aggM), slowLength);
def rawCALLM =
    EMAFastM crosses above EMASlowM within 1 bars;
def rawPUTM =
    EMAFastM crosses below EMASlowM within 1 bars;
AddChartBubble(
    showCALLM and firstBarToday and rawCALLM,
    low - atr * 2.2,
    "CALLM",
    GlobalColor("CALL"),
    no
);
AddChartBubble(
    showPUTM and firstBarToday and rawPUTM,
    high + atr * 2.2,
    "PUTM",
    GlobalColor("PUT"),
    yes
);