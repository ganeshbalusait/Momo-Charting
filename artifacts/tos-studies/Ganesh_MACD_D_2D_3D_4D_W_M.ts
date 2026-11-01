# MACD ONLY - D / 2D / 3D / 4D / W / M
# Fixed: one bubble per day only
# MACD 6/12/8 Value crosses Avg within 1 bars
input showCallD  = yes;
input showCall2D = yes;
input showCall3D = yes;
input showCall4D = yes;
input showCallW  = yes;
input showCallM  = yes;
input showPutD  = yes;
input showPut2D = yes;
input showPut3D = yes;
input showPut4D = yes;
input showPutW  = yes;
input showPutM  = yes;
DefineGlobalColor("CALL", Color.GREEN);
DefineGlobalColor("PUT", Color.RED);
def APD  = AggregationPeriod.DAY;
def AP2D = AggregationPeriod.TWO_DAYS;
def AP3D = AggregationPeriod.THREE_DAYS;
def AP4D = AggregationPeriod.FOUR_DAYS;
def APW  = AggregationPeriod.WEEK;
def APM  = AggregationPeriod.MONTH;
# New chart day
def today = GetYYYYMMDD();
def newDay = today != today[1];
# =========================
# DAILY MACD
# =========================
def MACDValueD =
    ExpAverage(close(period = APD), 6) -
    ExpAverage(close(period = APD), 12);
def MACDAvgD =
    ExpAverage(MACDValueD, 8);
def rawCallD =
    MACDValueD crosses above MACDAvgD within 1 bars;
def rawPutD =
    MACDValueD crosses below MACDAvgD within 1 bars;
def firedCallD =
    CompoundValue(
        1,
        if newDay then 0
        else if rawCallD then 1
        else firedCallD[1],
        0
    );
def callDPrint =
    rawCallD and firedCallD[1] == 0;
def firedPutD =
    CompoundValue(
        1,
        if newDay then 0
        else if rawPutD then 1
        else firedPutD[1],
        0
    );
def putDPrint =
    rawPutD and firedPutD[1] == 0;
AddChartBubble(showCallD and callDPrint, low, "MACD-D", GlobalColor("CALL"), no);
AddChartBubble(showPutD and putDPrint, high, "MACD-D", GlobalColor("PUT"), yes);
# =========================
# 2D MACD
# =========================
def MACDValue2D =
    ExpAverage(close(period = AP2D), 6) -
    ExpAverage(close(period = AP2D), 12);
def MACDAvg2D =
    ExpAverage(MACDValue2D, 8);
def rawCall2D =
    MACDValue2D crosses above MACDAvg2D within 1 bars;
def rawPut2D =
    MACDValue2D crosses below MACDAvg2D within 1 bars;
def firedCall2D =
    CompoundValue(
        1,
        if newDay then 0
        else if rawCall2D then 1
        else firedCall2D[1],
        0
    );
def call2DPrint =
    rawCall2D and firedCall2D[1] == 0;
def firedPut2D =
    CompoundValue(
        1,
        if newDay then 0
        else if rawPut2D then 1
        else firedPut2D[1],
        0
    );
def put2DPrint =
    rawPut2D and firedPut2D[1] == 0;
AddChartBubble(showCall2D and call2DPrint, low, "MACD-2D", GlobalColor("CALL"), no);
AddChartBubble(showPut2D and put2DPrint, high, "MACD-2D", GlobalColor("PUT"), yes);
# =========================
# 3D MACD
# =========================
def MACDValue3D =
    ExpAverage(close(period = AP3D), 6) -
    ExpAverage(close(period = AP3D), 12);
def MACDAvg3D =
    ExpAverage(MACDValue3D, 8);
def rawCall3D =
    MACDValue3D crosses above MACDAvg3D within 1 bars;
def rawPut3D =
    MACDValue3D crosses below MACDAvg3D within 1 bars;
def firedCall3D =
    CompoundValue(
        1,
        if newDay then 0
        else if rawCall3D then 1
        else firedCall3D[1],
        0
    );
def call3DPrint =
    rawCall3D and firedCall3D[1] == 0;
def firedPut3D =
    CompoundValue(
        1,
        if newDay then 0
        else if rawPut3D then 1
        else firedPut3D[1],
        0
    );
def put3DPrint =
    rawPut3D and firedPut3D[1] == 0;
AddChartBubble(showCall3D and call3DPrint, low, "MACD-3D", GlobalColor("CALL"), no);
AddChartBubble(showPut3D and put3DPrint, high, "MACD-3D", GlobalColor("PUT"), yes);
# =========================
# 4D MACD
# =========================
def MACDValue4D =
    ExpAverage(close(period = AP4D), 6) -
    ExpAverage(close(period = AP4D), 12);
def MACDAvg4D =
    ExpAverage(MACDValue4D, 8);
def rawCall4D =
    MACDValue4D crosses above MACDAvg4D within 1 bars;
def rawPut4D =
    MACDValue4D crosses below MACDAvg4D within 1 bars;
def firedCall4D =
    CompoundValue(
        1,
        if newDay then 0
        else if rawCall4D then 1
        else firedCall4D[1],
        0
    );
def call4DPrint =
    rawCall4D and firedCall4D[1] == 0;
def firedPut4D =
    CompoundValue(
        1,
        if newDay then 0
        else if rawPut4D then 1
        else firedPut4D[1],
        0
    );
def put4DPrint =
    rawPut4D and firedPut4D[1] == 0;
AddChartBubble(showCall4D and call4DPrint, low, "MACD-4D", GlobalColor("CALL"), no);
AddChartBubble(showPut4D and put4DPrint, high, "MACD-4D", GlobalColor("PUT"), yes);
# =========================
# WEEKLY MACD
# =========================
def MACDValueW =
    ExpAverage(close(period = APW), 6) -
    ExpAverage(close(period = APW), 12);
def MACDAvgW =
    ExpAverage(MACDValueW, 8);
def rawCallW =
    MACDValueW crosses above MACDAvgW within 1 bars;
def rawPutW =
    MACDValueW crosses below MACDAvgW within 1 bars;
def firedCallW =
    CompoundValue(
        1,
        if newDay then 0
        else if rawCallW then 1
        else firedCallW[1],
        0
    );
def callWPrint =
    rawCallW and firedCallW[1] == 0;
def firedPutW =
    CompoundValue(
        1,
        if newDay then 0
        else if rawPutW then 1
        else firedPutW[1],
        0
    );
def putWPrint =
    rawPutW and firedPutW[1] == 0;
AddChartBubble(showCallW and callWPrint, low, "MACD-W", GlobalColor("CALL"), no);
AddChartBubble(showPutW and putWPrint, high, "MACD-W", GlobalColor("PUT"), yes);
# =========================
# MONTHLY MACD
# =========================
def MACDValueM =
    ExpAverage(close(period = APM), 6) -
    ExpAverage(close(period = APM), 12);
def MACDAvgM =
    ExpAverage(MACDValueM, 8);
def rawCallM =
    MACDValueM crosses above MACDAvgM within 1 bars;
def rawPutM =
    MACDValueM crosses below MACDAvgM within 1 bars;
def firedCallM =
    CompoundValue(
        1,
        if newDay then 0
        else if rawCallM then 1
        else firedCallM[1],
        0
    );
def callMPrint =
    rawCallM and firedCallM[1] == 0;
def firedPutM =
    CompoundValue(
        1,
        if newDay then 0
        else if rawPutM then 1
        else firedPutM[1],
        0
    );
def putMPrint =
    rawPutM and firedPutM[1] == 0;
AddChartBubble(showCallM and callMPrint, low, "MACD-M", GlobalColor("CALL"), no);
AddChartBubble(showPutM and putMPrint, high, "MACD-M", GlobalColor("PUT"), yes);