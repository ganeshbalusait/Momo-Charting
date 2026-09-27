// Desktop framing pads the price range 10% each way and then maps it into the
// middle 76% of the pane, so candles get ~63% of the height. That reads as
// breathing room on a wide pane; on a phone it is a third of the screen showing
// nothing. Phones get tighter padding and an asymmetric margin - a bigger
// bottom margin keeps the volume histogram (top: 0.78) clear of the candles.
const DESKTOP_MARGINS = Object.freeze({ top: 0.12, bottom: 0.12 });
const PHONE_MARGINS = Object.freeze({ top: 0.06, bottom: 0.16 });

const DESKTOP_PAD_RATIO = 0.10;
const PHONE_PAD_RATIO = 0.04;
const MINIMUM_PAD = 0.03;

export function priceScaleMargins(isPhone) {
  return isPhone ? PHONE_MARGINS : DESKTOP_MARGINS;
}

export function pricePaddingFor(candleSpan, isPhone) {
  const ratio = isPhone ? PHONE_PAD_RATIO : DESKTOP_PAD_RATIO;
  return Math.max(Number(candleSpan || 0) * ratio, MINIMUM_PAD);
}
