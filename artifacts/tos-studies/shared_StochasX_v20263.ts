shared_StochasX_v20263:




#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/


input over_bought = 90;
input over_sold = 10;
input KPeriod = 8;
input DPeriod = 8;
input priceH = high;
input priceL = low;
input priceC = close;
input averageType = AverageType.WEIGHTED;
input showBreakoutSignals = {default "On FastD", "No"};
input showOverextended = No;


# ===== Limit plots to last N CALENDAR days (Extended Hours safe) =====
input limitToLastNDays = yes;
input daysBack = 2;   # 1=today only, 2=today+prior day, etc.


def yyyymmdd = GetYYYYMMDD();
def newDay = yyyymmdd <> yyyymmdd[1];


def dayIndex = CompoundValue(1, dayIndex[1] + (if newDay then 1 else 0), 0);
def lastDayIndex = HighestAll(dayIndex);


def showOK = if limitToLastNDays
             then dayIndex >= (lastDayIndex - (daysBack - 1))
             else 1;


def FastK = reference StochasticFull(over_bought, over_sold, KPeriod, DPeriod, priceH, priceL, priceC, 1, averageType).FullK;
def FastD = reference StochasticFull(over_bought, over_sold, KPeriod, DPeriod, priceH, priceL, priceC, 1, averageType).FullD;
def OverBought = over_bought;
def OverSold = over_sold;


def upD = FastD crosses above OverSold;
def downD = FastD crosses below OverBought;
def soldD = FastD <= over_sold;
def boughtD = FastD >= over_bought;


plot UpSignal;
plot UpSignal2;
plot DownSignal;
plot DownSignal2;


switch (showBreakoutSignals) {
case "No":
    UpSignal = Double.NaN;
    UpSignal2 = Double.NaN;
    DownSignal = Double.NaN;
    DownSignal2 = Double.NaN;


case "On FastD":
    UpSignal     = if showOK and upD then OverSold else Double.NaN;
    DownSignal   = if showOK and downD then OverBought else Double.NaN;


    UpSignal2    = if showOK and showOverextended and soldD then Round(FastD, 0) else Double.NaN;
    DownSignal2  = if showOK and showOverextended and boughtD then Round(FastD, 0) else Double.NaN;
}


# Hide logic
UpSignal.SetHiding(showBreakoutSignals == showBreakoutSignals."No" or !showOK);
DownSignal.SetHiding(showBreakoutSignals == showBreakoutSignals."No" or !showOK);


UpSignal2.SetHiding(!showOverextended or !showOK);
DownSignal2.SetHiding(!showOverextended or !showOK);


DefineGlobalColor("Above", CreateColor(0, 66, 102));
DefineGlobalColor("Below", CreateColor(95, 0, 102));
DefineGlobalColor("Oversold", Color.LIME);
DefineGlobalColor("Overbought", Color.LIGHT_RED);


UpSignal.SetDefaultColor(GlobalColor("Oversold"));
UpSignal.SetLineWeight(5);
UpSignal.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_DOWN);


DownSignal.SetDefaultColor(GlobalColor("Overbought"));
DownSignal.SetLineWeight(5);
DownSignal.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_UP);


UpSignal2.SetDefaultColor(GlobalColor("Below"));
UpSignal2.SetLineWeight(5);
UpSignal2.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_DOWN);


DownSignal2.SetDefaultColor(GlobalColor("Above"));
DownSignal2.SetLineWeight(5);
DownSignal2.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_UP);


  



=======


shared_StochasX_v20263:




#Author: @clarkalphas | Credit: Trading Alphas (c) 2026 | Website: https://www.tradingalphas.com/


input over_bought = 90;
input over_sold = 10;
input KPeriod = 8;
input DPeriod = 8;
input priceH = high;
input priceL = low;
input priceC = close;
input averageType = AverageType.WEIGHTED;
input showBreakoutSignals = {default "On FastD", "No"};
input showOverextended = No;


# ===== Limit plots to last N CALENDAR days (Extended Hours safe) =====
input limitToLastNDays = yes;
input daysBack = 2;   # 1=today only, 2=today+prior day, etc.


def yyyymmdd = GetYYYYMMDD();
def newDay = yyyymmdd <> yyyymmdd[1];


def dayIndex = CompoundValue(1, dayIndex[1] + (if newDay then 1 else 0), 0);
def lastDayIndex = HighestAll(dayIndex);


def showOK = if limitToLastNDays
             then dayIndex >= (lastDayIndex - (daysBack - 1))
             else 1;


def FastK = reference StochasticFull(over_bought, over_sold, KPeriod, DPeriod, priceH, priceL, priceC, 1, averageType).FullK;
def FastD = reference StochasticFull(over_bought, over_sold, KPeriod, DPeriod, priceH, priceL, priceC, 1, averageType).FullD;
def OverBought = over_bought;
def OverSold = over_sold;


def upD = FastD crosses above OverSold;
def downD = FastD crosses below OverBought;
def soldD = FastD <= over_sold;
def boughtD = FastD >= over_bought;


plot UpSignal;
plot UpSignal2;
plot DownSignal;
plot DownSignal2;


switch (showBreakoutSignals) {
case "No":
    UpSignal = Double.NaN;
    UpSignal2 = Double.NaN;
    DownSignal = Double.NaN;
    DownSignal2 = Double.NaN;


case "On FastD":
    UpSignal     = if showOK and upD then OverSold else Double.NaN;
    DownSignal   = if showOK and downD then OverBought else Double.NaN;


    UpSignal2    = if showOK and showOverextended and soldD then Round(FastD, 0) else Double.NaN;
    DownSignal2  = if showOK and showOverextended and boughtD then Round(FastD, 0) else Double.NaN;
}


# Hide logic
UpSignal.SetHiding(showBreakoutSignals == showBreakoutSignals."No" or !showOK);
DownSignal.SetHiding(showBreakoutSignals == showBreakoutSignals."No" or !showOK);


UpSignal2.SetHiding(!showOverextended or !showOK);
DownSignal2.SetHiding(!showOverextended or !showOK);


DefineGlobalColor("Above", CreateColor(0, 66, 102));
DefineGlobalColor("Below", CreateColor(95, 0, 102));
DefineGlobalColor("Oversold", Color.LIME);
DefineGlobalColor("Overbought", Color.LIGHT_RED);


UpSignal.SetDefaultColor(GlobalColor("Oversold"));
UpSignal.SetLineWeight(5);
UpSignal.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_DOWN);


DownSignal.SetDefaultColor(GlobalColor("Overbought"));
DownSignal.SetLineWeight(5);
DownSignal.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_UP);


UpSignal2.SetDefaultColor(GlobalColor("Below"));
UpSignal2.SetLineWeight(5);
UpSignal2.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_DOWN);


DownSignal2.SetDefaultColor(GlobalColor("Above"));
DownSignal2.SetLineWeight(5);
DownSignal2.SetPaintingStrategy(PaintingStrategy.BOOLEAN_WEDGE_UP);