#Author: @clarkalphas | Credit: Trading Alphas (c) 2025 | Website: https://www.tradingalphas.com/
declare upper;
input relVolLength = 50;
input numDev = 1.5;
input borderhighlightRelVol = yes;  ## Toggle for RelVol border highlighting
input borderhighlightVolAvg = yes;  ## Toggle for Volume >= VolAvg border highlighting
# Calculate Relative Volume and Average Volume
def rawRelVol = (volume - Average(volume, relVolLength)) / StDev(volume, relVolLength);
def isAboveThreshold = rawRelVol >= numDev;
def VolAvg = Average(volume, relVolLength);
def volumeIsAboveAvg = volume >= VolAvg;
# Volume Breakdown Settings
def buying = volume * (close - low) / (high - low);
def selling = volume * (high - close) / (high - low);
def isBullish = buying >= selling;
def o = open;
def h = high;
def l = low;
def c = close;
def charttype = ChartType.CANDLE;
# Define border conditions
def bullishBorderCond = borderhighlightRelVol and isAboveThreshold and isBullish;
def bearishBorderCond = borderhighlightRelVol and isAboveThreshold and !isBullish;
def sellVolGreaterThanBuyVol = borderhighlightVolAvg and volumeIsAboveAvg and selling > buying;
def buyVolGreaterThanSellVol = borderhighlightVolAvg and volumeIsAboveAvg and buying >= selling;
# Draw borders based on RelVol
def borderBuyReVol = if borderhighlightVolAvg and bullishBorderCond then 1 else 0;
def oBRV = if borderBuyReVol
         then if o < c then c else o
         else Double.NaN;
def cBRV = if borderBuyReVol
         then if o < c then o else c
         else Double.NaN;
def hBRV = if borderBuyReVol
         then h
         else Double.NaN;
def lBRV = if borderBuyReVol
         then l
         else Double.NaN;
def borderSellReVol = if borderhighlightVolAvg and bearishBorderCond then 1 else 0;
def oSRV = if borderSellReVol
         then if o < c then c else o
         else Double.NaN;
def cSRV = if borderSellReVol
         then if o < c then o else c
         else Double.NaN;
def hSRV = if borderSellReVol
         then h
         else Double.NaN;
def lSRV = if borderSellReVol
         then l
         else Double.NaN;
AddChart(growColor = Color.MAGENTA, fallColor = Color.MAGENTA, neutralColor = Color.MAGENTA, high = hSRV, low = lSRV, open = cSRV, close = oSRV, type = charttype);
AddChart(growColor = Color.CYAN, fallColor = Color.CYAN, neutralColor = Color.CYAN, high = hBRV, low = lBRV, open = cBRV, close = oBRV, type = charttype);
# Draw borders based on Volume >= VolAvg
def borderBuyGrtAvg = if borderhighlightVolAvg and buyVolGreaterThanSellVol then 1 else 0;
def oBGA = if borderBuyGrtAvg
         then if o < c then c else o
         else Double.NaN;
def cBGA = if borderBuyGrtAvg
         then if o < c then o else c
         else Double.NaN;
def hBGA = if borderBuyGrtAvg
         then h
         else Double.NaN;
def lBGA = if borderBuyGrtAvg
         then l
         else Double.NaN;
AddChart(growColor = Color.CYAN, fallColor = Color.CYAN, neutralColor = Color.CYAN, high = hBGA, low = lBGA, open = cBGA, close = oBGA, type = charttype);
def borderSellGrtAvg = if borderhighlightVolAvg and sellVolGreaterThanBuyVol then 1 else 0;
def oSGA = if borderSellGrtAvg
         then if o < c then c else o
         else Double.NaN;
def cSGA = if borderSellGrtAvg
         then if o < c then o else c
         else Double.NaN;
def hSGA = if borderSellGrtAvg
         then h
         else Double.NaN;
def lSGA = if borderSellGrtAvg
         then l
         else Double.NaN;
AddChart(growColor = Color.MAGENTA, fallColor = Color.MAGENTA, neutralColor = Color.MAGENTA, high = hSGA, low = lSGA, open = cSGA, close = oSGA, type = charttype);
# Plot Relative Volume values above or below based on buying/selling
plot RelVolAbove = if isAboveThreshold and !isBullish then round(rawRelVol,1) else Double.NaN;
RelVolAbove.SetDefaultColor(Color.Magenta);
RelVolAbove.SetStyle(Curve.FIRM);
RelVolAbove.HideTitle();
RelVolAbove.HideBubble();
RelVolAbove.SetPaintingStrategy(PaintingStrategy.VALUES_ABOVE);
plot RelVolBelow = if isAboveThreshold and isBullish then round(rawRelVol,1) else Double.NaN;
RelVolBelow.SetDefaultColor(Color.Cyan);
RelVolBelow.SetStyle(Curve.FIRM);
RelVolBelow.HideTitle();
RelVolBelow.HideBubble();
RelVolBelow.SetPaintingStrategy(PaintingStrategy.VALUES_BELOW);