#
# Charles Schwab & Co. (c) 2008-2026
#
input marketThreshold = 0.0025;
input timeFrame = {default DAY, "2 DAYS", "3 DAYS", "4 DAYS", WEEK, MONTH, "OPT EXP", QUARTER, YEAR};
input showOnlyToday = no;
input applyPersonsLevelsFilter = yes;
assert(marketThreshold >= 0, "'market threshold' must not be negative: " + marketThreshold);
def marketType = {default DISABLED, NEUTRAL, BEARISH, BULLISH};
def PP2 = high(period = timeFrame)[2] + low(period = timeFrame)[2] + close(period = timeFrame)[2];
marketType = if !applyPersonsLevelsFilter then marketType.DISABLED else
if PP2[-1] > (PP2[-1] + PP2 + PP2[1]) / 3 + marketThreshold then marketType.BULLISH else
if PP2[-1] < (PP2[-1] + PP2 + PP2[1]) / 3 - marketThreshold then marketType.BEARISH else marketType.NEUTRAL;
plot R3;
plot R2;
plot R1;
plot RR;
plot PP;
plot SS;
plot S1;
plot S2;
plot S3;
if showOnlyToday and !IsNaN(close(period = timeFrame)[-1])
then {
    R1 = Double.NaN;
    R2 = Double.NaN;
    R3 = Double.NaN;
    PP = Double.NaN;
    S1 = Double.NaN;
    S2 = Double.NaN;
    S3 = Double.NaN;
} else {
    PP = (high(period = timeFrame)[1] + low(period = timeFrame)[1] + close(period = timeFrame)[1]) / 3;
    R1 = 2 * PP - low(period = timeFrame)[1];
    R2 = PP + high(period = timeFrame)[1] - low(period = timeFrame)[1];
    R3 = R2 + high(period = timeFrame)[1] - low(period = timeFrame)[1];
    S1 = 2 * PP - high(period = timeFrame)[1];
    S2 = PP - high(period = timeFrame)[1] + low(period = timeFrame)[1];
    S3 = S2 - high(period = timeFrame)[1] + low(period = timeFrame)[1];
}
RR = if (marketType == marketType.BEARISH or marketType == marketType.NEUTRAL) then R1 else R2;
SS = if (marketType == marketType.BULLISH or marketType == marketType.NEUTRAL) then S1 else S2;
RR.setHiding(!applyPersonsLevelsFilter);
R1.setHiding(applyPersonsLevelsFilter);
R2.setHiding(applyPersonsLevelsFilter);
R3.hide();
SS.setHiding(!applyPersonsLevelsFilter);
S1.setHiding(applyPersonsLevelsFilter);
S2.setHiding(applyPersonsLevelsFilter);
S3.hide();
PP.SetDefaultColor(GetColor(0));
R1.SetDefaultColor(GetColor(5));
R2.SetDefaultColor(GetColor(5));
R3.SetDefaultColor(GetColor(5));
S1.SetDefaultColor(GetColor(6));
S2.SetDefaultColor(GetColor(6));
S3.SetDefaultColor(GetColor(6));
SS.DefineColor("S1", GetColor(6));
SS.DefineColor("S2", GetColor(6));
SS.AssignValueColor(if SS == S1 then SS.color("S1") else SS.color("S2"));
RR.DefineColor("R1", GetColor(5));
RR.DefineColor("R2", GetColor(5));
RR.AssignValueColor(if RR == R1 then RR.color("R1") else RR.color("R2"));
PP.SetStyle(Curve.SHORT_DASH);
RR.SetStyle(Curve.SHORT_DASH);
R1.SetStyle(Curve.SHORT_DASH);
R2.SetStyle(Curve.SHORT_DASH);
R3.SetStyle(Curve.SHORT_DASH);
SS.SetStyle(Curve.SHORT_DASH);
S1.SetStyle(Curve.SHORT_DASH);
S2.SetStyle(Curve.SHORT_DASH);
S3.SetStyle(Curve.SHORT_DASH);
def paintingStrategy = if timeframe == timeframe.WEEK then PaintingStrategy.LINE_VS_TRIANGLES else if timeFrame == timeFrame.MONTH or timeFrame == timeFrame."OPT EXP" or timeFrame == timeFrame.QUARTER or timeFrame == timeFrame.YEAR then PaintingStrategy.LINE_VS_SQUARES else PaintingStrategy.LINE_VS_POINTS;
PP.SetPaintingStrategy(paintingStrategy);
RR.SetPaintingStrategy(paintingStrategy);
R1.SetPaintingStrategy(paintingStrategy);
R2.SetPaintingStrategy(paintingStrategy);
R3.SetPaintingStrategy(paintingStrategy);
SS.SetPaintingStrategy(paintingStrategy);
S1.SetPaintingStrategy(paintingStrategy);
S2.SetPaintingStrategy(paintingStrategy);
S3.SetPaintingStrategy(paintingStrategy);