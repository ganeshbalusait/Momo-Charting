## ADX ##
def length2 = 10;
def averageType = AverageType.WILDERS;
def hiDiff = high - high[1];
def loDiff = low[1] - low;
def plusDM = if hiDiff > loDiff and hiDiff > 0 then hiDiff else 0;
def minusDM =  if loDiff > hiDiff and loDiff > 0 then loDiff else 0;
def ATR = MovingAverage(averageType, TrueRange(high, close, low), length2);
def "DI+" = 100 * MovingAverage(averageType, plusDM, length2) / ATR;
def "DI-" = 100 * MovingAverage(averageType, minusDM, length2) / ATR;
def DX = if ("DI+" + "DI-" > 0) then 100 * AbsValue("DI+" - "DI-") / ("DI+" + "DI-") else 0;
def ADX = MovingAverage(averageType, DX, length2);
def Strong = 25;
#def Weak = 20;
def Strength = ADX is greater than or equal to Strong;
def Weak = ADX is less than Strong;
def ADXBear = ADX and "DI-" is greater than or equal to Strong;
def ADXBull = ADX and "DI+" is greater than or equal to Strong;
def WeakBear = "DI-" is less than Strong;
def WeakBull = "DI+" is less than Strong;
def Bull = "DI+" is greater than or equal to "DI-";
def Bear = "DI-" is greater than or equal to "DI+";
def ADXWeakBear = WeakBear and Strength and Bull;
def ADXWeakBull = WeakBull and Strength and Bear;
def Call = "DI+" crosses above Strong;
def Put = "DI-" crosses above Strong;
## MACD_EXPONENTIAL ##
#def Value1 = MovingAverage(AverageType.EXPONENTIAL, close, 6) - MovingAverage(AverageType.EXPONENTIAL, close, 12);
#def Avg1 = MovingAverage(AverageType.EXPONENTIAL, Value1, 8);
#def Diff1 = Value1 - Avg1;
#def UpSignal1 = Diff1 crosses above ZeroLine;
#def DownSignal1 = Diff1 crosses below ZeroLine;
## MA ##
def EMA9 = MovingAverage(AverageType.EXPONENTIAL, close, 9);
def EMA20 = MovingAverage(AverageType.EXPONENTIAL, close, 20);
def EMA4 = MovingAverage(AverageType.EXPONENTIAL, close, 4);
def EMA8 = MovingAverage(AverageType.EXPONENTIAL, close, 8);
## BOLLINGER ##
#input length = 20;
#input Num_Dev = 2.0;
#def mean = MovingAverage(averageType.Simple, close, length);
#def sd = stDev(close, length) * Num_Dev;
#def upper = mean + sd;
#def lower = mean - sd;
#def width = (upper - lower) / mean;
def EMABull = EMA9 is greater than or equal to EMA20;
def EMABear = EMA9 is less than or equal to EMA20;
def EMABull1m = EMA4 crosses above EMA8;
def EMABear1m = EMA4 crosses below EMA8;
def EMABull5m = EMA9 crosses above EMA20;
def EMABear5m = EMA9 crosses below EMA20;
## SQUEEZE ##
#Factor: Low Squeeze = 2.0, High Squeeze = 1.0, Mid Squeeze = 1.5
def NS = TTM_Squeeze(nk = 2.0).SqueezeAlert > 0;
#lowsqueeze
def LS = TTM_Squeeze(nk = 2.0).SqueezeAlert == 0;
#def LSC = if LS then LSC[1]+1 else 0;
def LF = if !LS and LS[1] then 1 else 0;
def LSF = if (!LS and LS[1]) or (!LS and LSF[1] > 0 and LSF[1] < 1) then LSF[1] + 1 else 0;
#regsqueeze
def MS = TTM_Squeeze().SqueezeAlert == 0;
#def MSC = if MS then MSC[1]+1 else 0;
def MF = if !MS and MS[1] then 1 else 0;
def MSF = if (!MS and MS[1]) or (!MS and MSF[1] > 0 and MSF[1] < 1) then MSF[1] + 1 else 0;
#reghistogram
def MSH = TTM_Squeeze(nk = 2.0).Histogram;
#highsqueeze
def HS = TTM_Squeeze(nk = 1.0).SqueezeAlert == 0;
#def HSC = if HS then HSC[1]+1 else 0;
def HF = if !HS and HS[1] then 1 else 0;
def HSF = if (!HS and HS[1]) or (!HS and HSF[1] > 0 and HSF[1] < 1) then HSF[1] + 1 else 0;
#lowdirection
def LD = TTM_Squeeze(nk = 2.0) > TTM_Squeeze(nk = 2.0)[1];
def LFD = if LF then LD else LFD[1];
#regdirection
def MD = TTM_Squeeze() > TTM_Squeeze()[1];
def MFD = if MF then MD else MFD[1];
#highdirection
def HD = TTM_Squeeze(nk = 1.0) > TTM_Squeeze(nk = 1.0)[1];
def HFD = if HF then HD else HFD[1];
## PAINTING ##
#def signalOffset = Average(TrueRange(high, close, low), 10) * 10;
#plot HS1 = if HS then high else double.nan;
#HS1.SetPaintingStrategy(paintingStrategy.BOOLEAN_WEDGE_UP);
#HS1.setDefaultColor(color.white);
#HS1.setLineWeight(5);
plot HS1 = if HSF and HFD then high else if HSF then low else Double.NaN;
HS1.SetPaintingStrategy(PaintingStrategy.BOOLEAN_POINTS);
HS1.SetDefaultColor(Color.WHITE);
HS1.SetLineWeight(5);
plot MS1 = if MSF and MFD then high else if MSF then low else Double.NaN;
MS1.SetPaintingStrategy(PaintingStrategy.BOOLEAN_POINTS);
MS1.SetDefaultColor(Color.ORANGE);
MS1.SetLineWeight(5);
plot LS1 = if LSF and LFD then high else if LSF then low else Double.NaN;
LS1.SetPaintingStrategy(PaintingStrategy.BOOLEAN_POINTS);
LS1.SetDefaultColor(Color.BLUE);
LS1.SetLineWeight(5);
DefineGlobalColor("LessBear", CreateColor(13, 99, 102));
DefineGlobalColor("LessBull", CreateColor(102, 0, 102));
DefineGlobalColor("Strong", Color.WHITE);
DefineGlobalColor("Weak", Color.VIOLET);
DefineGlobalColor("Bear", Color.MAGENTA);
DefineGlobalColor("Bull", Color.CYAN);
## COLOR BARS ##
AssignPriceColor(
if HS then Color.WHITE else #if (HSF and HFD) then color.cyan else if HSF then color.magenta else
if MS then Color.ORANGE else #if (MSF and MFD) then color.cyan else if MSF then color.magenta else
if MSF and MFD then color.cyan else
if MSF then color.magenta else
if LSF and LFD then color.cyan else
if LSF then color.magenta else
if LS and ADXBull then Color.CYAN else 
if LS and ADXBear then Color.MAGENTA else
if EMABull5m then Color.CYAN else 
if EMABear5m then Color.MAGENTA else
if LS and Call then Color.CYAN else 
if LS and Put then Color.MAGENTA else
#if LS and ADXWeakBull then GlobalColor("LessBear") else if LS and ADXWeakBear then GlobalColor("LessBull") else
if ADXBull then GlobalColor("Bull") else if ADXBear then GlobalColor("Bear") else
if Call then Color.CYAN else if Put then Color.MAGENTA else
if MSH >=0 then
if MSH > MSH[1] then color.CYAN else GlobalColor("LessBull") else
if MSH < MSH[1] then color.MAGENTA else GlobalColor("LessBear"));
#if LS and EMABull then GlobalColor("LessBear") else if LS and EMABear then GlobalColor("LessBull") else
#if LS and EMABull then GlobalColor("LessBear") else if LS and EMABear then GlobalColor("LessBull") else
#if LS and WeakBull then GlobalColor("LessBear") else if LS and WeakBear then GlobalColor("LessBull") else
####if LS then Color.BLUE else #if (LSF and LFD) then color.cyan else if LSF then color.magenta else
#if ADXWeakBull then GlobalColor("LessBull") else if ADXWeakBear then GlobalColor("LessBear") else
#if Bull then GlobalColor("LessBear") else if Bear then GlobalColor("LessBull") else
## BORDER BARS ## 
input borderhighlight = yes;
def o = open;
def h = high;
def l = low;
def c = close;
def charttype = ChartType.CANDLE;
def cond = if borderhighlight and c >= o then 1 else 0;
def o1 = if cond
         then if o < c then c else o
         else Double.NaN;
def c1 = if cond
         then if o < c then o else c
         else Double.NaN;
def h1 = if cond
         then h
         else Double.NaN;
def l1 = if cond
         then l
         else Double.NaN;
AddChart(growColor = Color.CYAN, fallColor = Color.CYAN, neutralColor = Color.CYAN, high = h1, low = l1, open = c1, close = o1, type = charttype);
def cond2 = if borderhighlight and c < o then 1 else 0;
def o2 = if cond2
         then if o < c then c else o
         else Double.NaN;
def c2 = if cond2
         then if o < c then o else c
         else Double.NaN;
def h2 = if cond2
         then h
         else Double.NaN;
def l2 = if cond2
         then l
         else Double.NaN;
AddChart(growColor = Color.MAGENTA, fallColor = Color.MAGENTA, neutralColor = Color.MAGENTA, high = h2, low = l2, open = c2, close = o2, type = charttype);
def condLF = if borderhighlight and LSF then 1 else 0;
def oLF = if condLF
         then if o < c then c else o
         else Double.NaN;
def cLF = if condLF
         then if o < c then o else c
         else Double.NaN;
def hLF = if condLF
         then h
         else Double.NaN;
def lLF = if condLF
         then l
         else Double.NaN;
AddChart(growColor = Color.MAGENTA, fallColor = Color.MAGENTA, neutralColor = Color.BLUE, high = hLF, low = lLF, open = cLF, close = oLF, type = charttype);
def condMF = if borderhighlight and MSF then 1 else 0;
def oMF = if condMF
         then if o < c then c else o
         else Double.NaN;
def cMF = if condMF
         then if o < c then o else c
         else Double.NaN;
def hMF = if condMF
         then h
         else Double.NaN;
def lMF = if condMF
         then l
         else Double.NaN;
AddChart(growColor = Color.MAGENTA, fallColor = Color.MAGENTA, neutralColor = Color.ORANGE, high = hMF, low = lMF, open = cMF, close = oMF, type = charttype);
def condHF = if borderhighlight and HSF then 1 else 0;
def oHF = if condHF
         then if o < c then c else o
         else Double.NaN;
def cHF = if condHF
         then if o < c then o else c
         else Double.NaN;
def hHF = if condHF
         then h
         else Double.NaN;
def lHF = if condHF
         then l
         else Double.NaN;
AddChart(growColor = Color.MAGENTA, fallColor = Color.MAGENTA, neutralColor = Color.WHITE, high = hHF, low = lHF, open = cHF, close = oHF, type = charttype);
def condL = if borderhighlight and LSF and LFD then 1 else 0;
def oL = if condL
         then if o < c then c else o
         else Double.NaN;
def cL = if condL
         then if o < c then o else c
         else Double.NaN;
def hL = if condL
         then h
         else Double.NaN;
def lL = if condL
         then l
         else Double.NaN;
AddChart(growColor = Color.CYAN, fallColor = Color.CYAN, neutralColor = Color.BLUE, high = hL, low = lL, open = cL, close = oL, type = charttype);
def condM = if borderhighlight and MSF and MFD then 1 else 0;
def oM = if condM
         then if o < c then c else o
         else Double.NaN;
def cM = if condM
         then if o < c then o else c
         else Double.NaN;
def hM = if condM
         then h
         else Double.NaN;
def lM = if condM
         then l
         else Double.NaN;
AddChart(growColor = Color.CYAN, fallColor = Color.CYAN, neutralColor = Color.ORANGE, high = hM, low = lM, open = cM, close = oM, type = charttype);
def condH = if borderhighlight and HSF and HFD then 1 else 0;
def oH = if condH
         then if o < c then c else o
         else Double.NaN;
def cH = if condH
         then if o < c then o else c
         else Double.NaN;
def hH = if condH
         then h
         else Double.NaN;
def lH = if condH
         then l
         else Double.NaN;
AddChart(growColor = Color.CYAN, fallColor = Color.CYAN, neutralColor = Color.WHITE, high = hH, low = lH, open = cH, close = oH, type = charttype);
######