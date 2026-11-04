// Sector heatmap, Robinhood "Sectors" style (trader ask, 2026-11-03): one card
// per sector holding that sector's top stocks, tiles sized by market cap,
// colored by today's move, headline % = the sector ETF. Everything here is pure
// so the panel's maths can be tested without a browser: the cards, color
// steps, and the two layouts (Robinhood rows, thinkorswim treemap).

// Read off the Robinhood card: AVGO +3.15% and META +2.76% are both the full
// green, AAPL -2.04% is already the full orange.
export const HEAT_CAP_PCT = 2.5;
export const DOWN_CAP_PCT = 2;

// The sector cards, Robinhood-style (trader, 2026-11-03: Robinhood shows "each
// sector multiple tickers" -- every card is that sector's top stocks, not just
// the watchlist). Each card: its name, the SPDR sector ETF whose move is the
// card's headline %, and its ~16 largest names with an approximate market cap
// in $B. The cap only sizes the tile until the real one arrives from the
// market-cap cache (momx/industry_lookup.py); tile area is sqrt(cap) anyway,
// so an estimate a few months stale changes nothing visible.
//
// Technology is exactly the card in the trader's Robinhood screenshot, which
// (unlike GICS) counts GOOGL/GOOG, AMZN and META as tech; the other cards
// therefore leave those names out so no ticker appears twice.
export const SECTOR_CARDS = [
  { name: "Technology", etf: "XLK", leaders: "NVDA:4400 AAPL:3900 MSFT:3800 GOOGL:2200 GOOG:2200 AMZN:2400 TSM:1200 META:1800 AVGO:1600 SKHY:500 MU:140 AMD:310 ASML:300 TCEHY:600 PLTR:420 CSCO:250", more: "ORCL:600 CRM:250 IBM:250 NOW:190 INTU:180 QCOM:180 APP:180 TXN:170 ADBE:160 AMAT:160 INTC:150 ANET:150 PANW:130 LRCX:120 CRWD:120 ADI:115 KLAC:110 DELL:90 CDNS:85 SNPS:80 MRVL:70 NET:70 SHOP:200 SMCI:30", about: "Chipmakers, software, internet platforms and the hardware they run on.", groups: "Semiconductors=NVDA AVGO TSM AMD MU ASML SKHY QCOM TXN AMAT INTC LRCX ADI KLAC MRVL|Software=MSFT ORCL CRM NOW INTU ADBE PLTR APP PANW CRWD SNPS CDNS NET SHOP|Internet=GOOGL GOOG META AMZN TCEHY|Hardware=AAPL CSCO ANET DELL SMCI IBM" },
  { name: "Financials", etf: "XLF", leaders: "JPM:820 V:680 MA:530 BAC:390 WFC:270 GS:240 MS:230 AXP:220 C:190 SCHW:170 BLK:170 SPGI:160 PGR:150 COF:140 HOOD:110 COIN:90", more: "BX:200 KKR:120 CME:100 CB:110 MMC:110 ICE:95 MCO:90 IBKR:90 PNC:80 APO:80 AON:80 USB:75 PYPL:70 BK:70 TFC:60 TRV:60 MET:55 ALL:50 AIG:45 NDAQ:45 SOFI:30 STT:30 RJF:30 AFRM:25", about: "Banks, card networks and fintech, brokers and asset managers, and insurers.", groups: "Banks=JPM BAC WFC C PNC USB TFC BK STT COF|Payments & fintech=V MA AXP PYPL SOFI AFRM HOOD COIN|Capital markets=GS MS SCHW BLK BX KKR APO IBKR RJF SPGI MCO CME ICE NDAQ|Insurance=PGR CB MMC AON TRV MET ALL AIG" },
  { name: "Healthcare", etf: "XLV", leaders: "LLY:900 JNJ:460 ABBV:390 UNH:300 ABT:230 MRK:230 TMO:200 ISRG:190 AMGN:170 BSX:150 PFE:145 GILD:140 SYK:140 DHR:140 VRTX:120 NVO:240", more: "AZN:240 NVS:240 MDT:115 HCA:100 CVS:90 MCK:90 CI:80 ELV:80 ZTS:70 REGN:65 ALNY:60 BDX:55 COR:55 IDXX:50 EW:45 ARGX:45 A:40 IQV:35 DXCM:30 HUM:30 BIIB:20 MRNA:12 HIMS:12 TEM:12", about: "Drugmakers, biotech, medical devices, insurers and providers, and lab tools.", groups: "Pharma=LLY JNJ ABBV MRK PFE NVO AZN NVS ZTS|Biotech=AMGN GILD VRTX REGN ALNY ARGX BIIB MRNA|Medical devices=ABT ISRG BSX SYK MDT BDX EW DXCM|Health services=UNH HCA CVS MCK CI ELV COR HUM HIMS|Tools & diagnostics=TMO DHR IDXX A IQV TEM" },
  { name: "Consumer Cyclical", etf: "XLY", leaders: "TSLA:1400 HD:380 MCD:220 BKNG:170 TJX:150 LOW:140 SBUX:100 NKE:95 ABNB:80 ORLY:80 CMG:60 MAR:75 RCL:85 DASH:110 GM:60 F:45", more: "CVNA:70 AZO:65 HLT:65 ROST:50 DHI:50 YUM:40 EBAY:40 LEN:35 CCL:35 EXPE:30 TSCO:30 LULU:25 PHM:25 ULTA:25 WSM:25 DRI:25 DECK:20 RL:20 BBY:18 DPZ:15 APTV:15 RIVN:15 NCLH:12 POOL:12", about: "What people buy when times are good: cars, stores, restaurants, travel and homes.", groups: "Autos=TSLA GM F RIVN APTV CVNA|Retail=HD LOW TJX ORLY AZO ROST EBAY TSCO ULTA WSM BBY POOL|Restaurants=MCD SBUX CMG YUM DRI DPZ DASH|Travel & leisure=BKNG ABNB MAR RCL HLT CCL EXPE NCLH|Apparel=NKE LULU DECK RL|Homebuilders=DHI LEN PHM" },
  { name: "Communication", etf: "XLC", leaders: "NFLX:510 TMUS:250 DIS:200 T:190 VZ:170 CMCSA:130 SPOT:130 RBLX:90 TTWO:45 EA:50 CHTR:50 WBD:40 RDDT:40 BIDU:35 LYV:33 TKO:30", more: "SE:90 NTES:80 AMX:30 VOD:25 FOXA:25 PINS:22 TU:22 BCE:20 RCI:20 NWSA:16 SNAP:15 TME:15 OMC:15 ROKU:12 LBRDK:12 IPG:10 FYBR:9 NYT:9 MTCH:8 SIRI:8 PARA:8 BILI:8", about: "Telecom carriers, streaming and entertainment, games, and social and online media.", groups: "Telecom=TMUS T VZ CMCSA CHTR AMX VOD TU BCE RCI LBRDK FYBR|Entertainment=NFLX DIS SPOT WBD LYV TKO FOXA ROKU PARA SIRI TME|Gaming=RBLX TTWO EA NTES SE|Internet & media=RDDT BIDU PINS SNAP MTCH BILI NWSA NYT OMC IPG" },
  { name: "Industrials", etf: "XLI", leaders: "GE:300 CAT:250 RTX:230 UBER:190 BA:170 HON:130 UNP:140 GEV:170 ETN:140 DE:130 LMT:110 ADP:120 PH:95 UPS:80 WM:90 NOC:85", more: "TT:90 CTAS:85 MMM:80 GD:80 TDG:80 ITW:75 EMR:75 RSG:70 CSX:65 PWR:60 FDX:60 CARR:60 JCI:60 HWM:60 NSC:55 PCAR:55 CPRT:50 LHX:50 AXON:50 URI:50 CMI:50 FAST:45 VRSK:40 ODFL:35", about: "Aerospace and defense, machinery and electrical equipment, transport, and business services.", groups: "Aerospace & defense=GE RTX BA LMT NOC GD TDG HWM LHX AXON|Machinery & equipment=CAT DE PH ITW EMR ETN GEV PWR TT CARR JCI CMI PCAR HON MMM|Transport=UBER UNP UPS CSX FDX NSC ODFL|Business services=ADP WM CTAS RSG CPRT URI FAST VRSK" },
  { name: "Consumer Defensive", etf: "XLP", leaders: "WMT:830 COST:420 PG:360 KO:300 PM:250 PEP:200 MO:100 MDLZ:80 CL:70 TGT:45 KMB:40 KDP:45 MNST:65 KHC:30 GIS:30 HSY:35", more: "UL:150 BTI:90 DEO:60 KR:45 KVUE:40 SYY:35 STZ:30 K:28 ADM:25 EL:25 CHD:25 DG:25 MKC:20 TSN:20 DLTR:20 CLX:17 HRL:15 BJ:13 CAG:12 SJM:12 CPB:12 TAP:12 CELH:10", about: "Everyday staples people buy in any economy: groceries, drinks, food and household goods.", groups: "Retail=WMT COST TGT KR DG DLTR BJ SYY|Beverages=KO PEP KDP MNST DEO STZ TAP CELH|Food=MDLZ KHC GIS HSY K ADM MKC TSN HRL CAG SJM CPB|Household & personal=PG CL KMB UL KVUE EL CHD CLX|Tobacco=PM MO BTI" },
  { name: "Energy", etf: "XLE", leaders: "XOM:490 CVX:270 COP:120 EOG:65 WMB:70 KMI:60 EPD:70 SLB:50 ET:60 MPC:55 PSX:55 OXY:45 VLO:45 BKR:45 HAL:22 FANG:42", more: "SHEL:210 TTE:140 ENB:95 BP:90 PBR:80 CNQ:65 EQNR:65 OKE:60 SU:50 LNG:50 TRGP:40 IMO:40 EQT:30 CVE:30 TPL:25 EXE:25 DVN:22 CTRA:20 AR:10 RRC:9 APA:8", about: "Oil and gas producers, the majors, pipelines, refiners and oilfield services.", groups: "Integrated=XOM CVX SHEL TTE BP PBR EQNR SU IMO CVE CNQ|Exploration & production=COP EOG OXY FANG DVN CTRA EQT EXE AR RRC APA TPL|Pipelines=WMB KMI EPD ET OKE ENB LNG TRGP|Refining=MPC PSX VLO|Services=SLB BKR HAL" },
  { name: "Utilities", etf: "XLU", leaders: "NEE:160 CEG:110 SO:100 DUK:95 VST:65 AEP:60 SRE:55 D:50 EXC:45 PCG:35 XEL:42 PEG:42 ED:36 ETR:38 WEC:35 NRG:30", more: "AWK:28 DTE:28 AEE:27 PPL:26 FE:24 ATO:24 ES:22 CNP:22 EIX:22 CMS:21 NI:19 LNT:16 EVRG:16 TLN:15 OKLO:15 PNW:10 WTRG:10 AES:9 OGE:9 BEP:8", about: "Electric, gas and water utilities, plus the power producers selling to data centers.", groups: "Power producers=CEG VST NRG TLN OKLO AES BEP|Electric utilities=NEE SO DUK AEP SRE D EXC PCG XEL PEG ED ETR WEC DTE AEE PPL FE ES CNP EIX CMS LNT EVRG PNW OGE|Gas & water=ATO NI AWK WTRG" },
  { name: "Real Estate", etf: "XLRE", leaders: "PLD:105 WELL:110 AMT:95 EQIX:80 SPG:60 O:52 DLR:55 PSA:50 CCI:45 CBRE:48 VICI:35 EXR:30 IRM:30 AVB:30 EQR:26 VTR:32", more: "CSGP:30 SBAC:22 WY:20 INVH:20 ESS:18 MAA:17 Z:17 ARE:15 SUI:15 KIM:15 AMH:14 UDR:13 REG:13 GLPI:13 LAMR:13 CPT:12 HST:12 BXP:12 ELS:12 OHI:12 DOC:12 FRT:9 CUBE:9 NNN:8", about: "REITs that own warehouses, data centers, towers, homes, stores and offices, plus real-estate services.", groups: "Industrial & data=PLD EQIX DLR IRM|Towers=AMT CCI SBAC LAMR|Residential=AVB EQR INVH ESS MAA AMH UDR CPT ELS SUI|Healthcare=WELL VTR OHI DOC|Retail & net lease=SPG O KIM REG FRT NNN VICI GLPI|Storage=PSA EXR CUBE|Office & other=ARE BXP HST WY|Services=CBRE CSGP Z" },
  { name: "Materials", etf: "XLB", leaders: "LIN:220 SHW:85 NEM:90 ECL:75 FCX:60 APD:65 CRH:70 CTVA:42 NUE:32 VMC:37 MLM:35 DD:32 PPG:26 DOW:18 IFF:20 ALB:10", more: "BHP:130 RIO:100 SCCO:90 AEM:70 VALE:45 WPM:45 FNV:35 IP:25 SW:22 LYB:20 STLD:20 PKG:19 RS:16 BALL:16 CF:15 AMCR:14 AVY:14 EMN:10 MP:10 AA:10 MOS:9 CLF:6", about: "Chemicals, miners and metals, gold, construction materials and packaging.", groups: "Chemicals=LIN SHW ECL APD DD PPG DOW IFF LYB EMN CTVA CF MOS|Mining & metals=FCX BHP RIO SCCO VALE ALB MP AA NUE STLD RS CLF|Gold & precious=NEM AEM WPM FNV|Construction materials=CRH VMC MLM|Packaging=IP SW PKG BALL AMCR AVY" },
].map((card) => {
  const parse = (list) => String(list || "").split(" ").filter(Boolean).map((pair) => {
    const [symbol, capB] = pair.split(":");
    return { symbol, cap: Number(capB) * 1e9 };
  });
  // leaders: the 16 on the card. more: the rest of the sector's big names,
  // shown only on the sector's own page (tap the card's name), like the
  // Robinhood sector page; they are quoted only while that page is open.
  // groups: the sector's sub-groups, shown as filter chips on its page
  // (Robinhood's "Electronic components / Software / ..." row).
  const groups = String(card.groups || "").split("|").filter(Boolean).map((part) => {
    const [label, symbols] = part.split("=");
    return { label, symbols: symbols.split(" ").filter(Boolean) };
  });
  return { ...card, leaders: parse(card.leaders), more: parse(card.more), groups };
});

// Every symbol the card grid needs quoted: all leaders plus the sector ETFs.
// A sector page adds its own "more" names (sectorPageSymbols).
export const SECTOR_SYMBOLS = [...new Set(SECTOR_CARDS.flatMap((card) => [...card.leaders.map((leader) => leader.symbol), card.etf]))];

export function sectorPageSymbols(name, cards = SECTOR_CARDS) {
  const card = cards.find((item) => item.name === name);
  return card ? card.more.map((item) => item.symbol) : [];
}

const finite = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};
const positive = (value) => {
  const number = finite(value);
  return number !== null && number > 0 ? number : null;
};

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// quotes: { SYMBOL: row } from /api/watchlist-quotes?profile=1 (lastPrice,
// change, changePct, marketCap).
//
// hideUnquoted: a name the quote feed answered with no price (a new listing,
// an OTC ADR) is left out instead of drawn as a dead grey tile. A name not in
// `quotes` at all has simply not been asked yet, and stays (grey) until it is.
function buildGroup(card, members, quotes, { hideUnquoted = true } = {}) {
  const tiles = members
    .map(({ symbol, cap }) => {
      const quote = quotes?.[symbol] || {};
      const liveCap = positive(quote.marketCap);
      const capWeight = liveCap ?? cap;
      return {
        symbol,
        changePct: finite(quote.changePct),
        change: finite(quote.change),
        lastPrice: finite(quote.lastPrice),
        marketCap: capWeight,
        estimatedCap: liveCap === null,
        capWeight,
        // Tile AREA follows sqrt(cap), as the Robinhood card visibly does
        // (MSFT and GOOGL are far closer in size than their caps).
        weight: Math.sqrt(capWeight),
        answered: Boolean(quotes && Object.prototype.hasOwnProperty.call(quotes, symbol)),
      };
    })
    .filter((tile) => !(hideUnquoted && tile.answered && tile.lastPrice === null))
    .map(({ answered, ...tile }) => tile)
    .sort((left, right) => right.weight - left.weight || left.symbol.localeCompare(right.symbol));

  const priced = tiles.filter((tile) => tile.changePct !== null);
  const pricedWeight = priced.reduce((sum, tile) => sum + tile.capWeight, 0);
  const tilesMove = pricedWeight > 0
    ? priced.reduce((sum, tile) => sum + tile.changePct * tile.capWeight, 0) / pricedWeight
    : null;
  // Headline % = the sector ETF's move today (the whole sector, as Robinhood
  // shows it); the cap-weighted move of the tiles only if the ETF has no quote.
  const etfMove = finite(quotes?.[card.etf]?.changePct);
  return {
    name: card.name,
    etf: card.etf,
    tiles,
    changePct: etfMove ?? tilesMove,
    moveSource: etfMove !== null ? "etf" : tilesMove !== null ? "tiles" : "none",
    up: priced.filter((tile) => tile.changePct > 0).length,
    down: priced.filter((tile) => tile.changePct < 0).length,
  };
}

// The card grid, in Robinhood's order: each sector's 16 leaders.
export function buildMarketSectorGroups(quotes = {}, { hideUnquoted = true, cards = SECTOR_CARDS } = {}) {
  return cards.map((card) => buildGroup(card, card.leaders, quotes, { hideUnquoted }));
}

// One sector's own page (trader, 2026-11-06: tapping a sector should open it
// "like robinhood"): the 16 leaders plus the rest of its big names.
export function buildSectorPage(name, quotes = {}, { hideUnquoted = true, cards = SECTOR_CARDS } = {}) {
  const card = cards.find((item) => item.name === name);
  if (!card) return null;
  const page = buildGroup(card, [...card.leaders, ...card.more], quotes, { hideUnquoted });
  page.about = card.about || "";
  page.subgroups = card.groups;
  page.tiles = page.tiles.map((tile) => ({ ...tile, name: companyName(quotes?.[tile.symbol]?.name, tile.symbol) }));
  return page;
}

// The tiles of one sub-group chip ("All" or null = every tile).
export function filterSectorTiles(page, label) {
  if (!page || !label || label === "All") return page ? page.tiles : [];
  const group = page.subgroups.find((item) => item.label === label);
  if (!group) return page.tiles;
  const wanted = new Set(group.symbols);
  return page.tiles.filter((tile) => wanted.has(tile.symbol));
}

// Schwab's quote description ("NVIDIA CORP", "ALPHABET INC CL A",
// "JPMORGAN CHASE & CO") -> the name a person reads ("NVIDIA", "Alphabet
// Class A", "JPMorgan Chase"), as Robinhood's list shows it. null when the
// feed has no description yet (the row then shows just the ticker).
const NAME_SUFFIXES = new Set([
  "INC", "INC.", "CORP", "CORP.", "CORPORATION", "CO", "CO.", "COMPANY", "LTD", "LTD.", "LIMITED", "PLC",
  "NV", "N.V.", "SA", "S.A.", "AG", "SE", "LP", "L.P.", "LLC", "HOLDINGS", "HLDGS", "HOLDING", "THE", "NEW",
  "COM", "COMMON", "STOCK", "SHS", "SHARES", "ORDINARY", "ADR", "ADS", "SPONSORED", "SPON", "REIT", "&",
]);
const NAME_WORDS = {
  NVIDIA: "NVIDIA", JPMORGAN: "JPMorgan", EBAY: "eBay", PAYPAL: "PayPal", SERVICENOW: "ServiceNow",
  "AT&T": "AT&T", IBM: "IBM", AMD: "AMD", ASML: "ASML", TSMC: "TSMC", UBS: "UBS", CVS: "CVS", HCA: "HCA",
  UPS: "UPS", RTX: "RTX", GE: "GE", AMETEK: "AMETEK", CME: "CME", MSCI: "MSCI", "S&P": "S&P", DR: "Dr",
  APPLOVIN: "AppLovin", CROWDSTRIKE: "CrowdStrike", DOORDASH: "DoorDash", AUTOZONE: "AutoZone",
  BLACKROCK: "BlackRock", NETFLIX: "Netflix", MCDONALDS: "McDonald's", "MCDONALD'S": "McDonald's",
  ROBINHOOD: "Robinhood", SOFI: "SoFi", GEVERNOVA: "GE Vernova", EXXON: "Exxon", CLOUDFLARE: "Cloudflare",
};
export function companyName(description, symbol = "") {
  const raw = String(description || "").replace(/\s+/g, " ").trim();
  if (!raw || raw.toUpperCase() === String(symbol).toUpperCase()) return null;
  let words = raw.split(" ");
  // "CL A" / "CLASS A" -> "Class A", kept; everything after it is legal noise.
  const classAt = words.findIndex((word, index) => /^(CL|CLASS)$/i.test(word) && /^[A-C]$/i.test(words[index + 1] || ""));
  const shareClass = classAt >= 0 ? `Class ${words[classAt + 1].toUpperCase()}` : "";
  if (classAt >= 0) words = words.slice(0, classAt);
  while (words.length > 1 && NAME_SUFFIXES.has(words[words.length - 1].toUpperCase().replace(/,$/, ""))) words.pop();
  const cased = words.map((word) => {
    const clean = word.replace(/,$/, "");
    const upper = clean.toUpperCase();
    if (NAME_WORDS[upper]) return NAME_WORDS[upper];
    if (/[&.]/.test(clean) && clean.length <= 5) return upper;
    return upper.charAt(0) + upper.slice(1).toLowerCase();
  });
  return [cased.join(" "), shareClass].filter(Boolean).join(" ") || null;
}

// Order of the stock list on a sector page: "size" (biggest first, as the
// heatmap reads), "high" (best move first) or "low" (worst first). Names with
// no move yet go last.
export const PAGE_SORT_MODES = ["size", "high", "low"];

export function sortSectorRows(tiles, mode = "size") {
  const list = Array.isArray(tiles) ? [...tiles] : [];
  if (mode === "high" || mode === "low") {
    const direction = mode === "high" ? -1 : 1;
    return list.sort((left, right) => {
      if (left.changePct === null || right.changePct === null) {
        if (left.changePct === right.changePct) return right.weight - left.weight;
        return left.changePct === null ? 1 : -1;
      }
      return (left.changePct - right.changePct) * direction || right.weight - left.weight;
    });
  }
  return list.sort((left, right) => right.weight - left.weight || left.symbol.localeCompare(right.symbol));
}

// Card order (trader, 2026-11-06: "sort by high/low"). "default" keeps the
// Robinhood order; "high" puts the best sector move first, "low" the worst.
// Cards with no move yet always go last, in their default order.
export const SORT_MODES = ["default", "high", "low"];

export function sortSectorGroups(groups, mode = "default") {
  const list = Array.isArray(groups) ? [...groups] : [];
  if (mode !== "high" && mode !== "low") return list;
  const direction = mode === "high" ? -1 : 1;
  return list
    .map((group, index) => ({ group, index }))
    .sort((left, right) => {
      const a = left.group.changePct;
      const b = right.group.changePct;
      if (a === null || b === null || a === undefined || b === undefined) {
        if ((a ?? null) === null && (b ?? null) === null) return left.index - right.index;
        return (a ?? null) === null ? 1 : -1;
      }
      return (a - b) * direction || left.index - right.index;
    })
    .map(({ group }) => group);
}

// Color bucket for a move. Discrete buckets (rather than a smooth gradient)
// keep neighbouring tiles readable as "a bit up" vs "a lot up", as in the
// reference screenshot. Returned as a level so CSS owns the actual colors.
export function heatLevel(changePct) {
  const pct = finite(changePct);
  if (pct === null) return { tone: "none", level: 0 };
  const size = Math.abs(pct);
  if (size < 0.05) return { tone: "flat", level: 0 };
  // Five steps, matched to the Robinhood card: GOOGL +0.29% pale, AMZN +1.17%
  // light, MSFT +1.90% / TSM +2.02% mid, NVDA +3.17% full.
  // Robinhood's orange saturates sooner than its green (AAPL -2.04% is full
  // orange while MSFT +1.90% / TSM +2.02% are not yet full green).
  const full = pct > 0 ? HEAT_CAP_PCT : DOWN_CAP_PCT;
  const level = size >= full ? 5 : size >= 1.5 ? 4 : size >= 1 ? 3 : size >= 0.5 ? 2 : 1;
  return { tone: pct > 0 ? "up" : "down", level };
}

export function formatSignedPct(value) {
  const pct = finite(value);
  if (pct === null) return "--";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

export function formatMarketCap(value) {
  const cap = positive(value);
  if (cap === null) return "--";
  if (cap >= 1e12) return `$${(cap / 1e12).toFixed(2)}T`;
  if (cap >= 1e9) return `$${(cap / 1e9).toFixed(1)}B`;
  if (cap >= 1e6) return `$${(cap / 1e6).toFixed(0)}M`;
  return `$${cap.toFixed(0)}`;
}

// --- Squarified treemap (Bruls, Huizing, van Wijk) -------------------------
// Lays `items` (each with a positive `weight`) into the rectangle
// {x, y, width, height}, biggest first from the top-left, keeping tiles as
// close to square as the weights allow. Returns [{ item, x, y, width, height }].

function worstRatio(row, side) {
  if (!row.length || side <= 0) return Infinity;
  const sum = row.reduce((total, area) => total + area, 0);
  const largest = Math.max(...row);
  const smallest = Math.min(...row);
  const sideSquared = side * side;
  const sumSquared = sum * sum;
  return Math.max((sideSquared * largest) / sumSquared, sumSquared / (sideSquared * smallest));
}

export function squarify(items, rect) {
  const width = Math.max(0, Number(rect?.width) || 0);
  const height = Math.max(0, Number(rect?.height) || 0);
  const list = (Array.isArray(items) ? items : [])
    .filter((item) => Number(item?.weight) > 0)
    .sort((left, right) => right.weight - left.weight);
  if (!list.length || width <= 0 || height <= 0) return [];
  const total = list.reduce((sum, item) => sum + item.weight, 0);
  const scale = (width * height) / total;
  const areas = list.map((item) => item.weight * scale);

  const out = [];
  let x = Number(rect?.x) || 0;
  let y = Number(rect?.y) || 0;
  let freeWidth = width;
  let freeHeight = height;
  let index = 0;

  while (index < list.length) {
    const side = Math.min(freeWidth, freeHeight);
    const row = [areas[index]];
    let next = index + 1;
    while (next < list.length && worstRatio([...row, areas[next]], side) <= worstRatio(row, side)) {
      row.push(areas[next]);
      next += 1;
    }
    const rowArea = row.reduce((sum, area) => sum + area, 0);
    // The last row fills whatever is left exactly, so rounding can never leave
    // a sliver or push past the card edge.
    const last = next >= list.length;
    if (freeWidth >= freeHeight) {
      // Lay the row as a column on the left.
      const columnWidth = last ? freeWidth : Math.min(freeWidth, rowArea / freeHeight);
      let cursor = y;
      row.forEach((area, offset) => {
        const tileHeight = offset === row.length - 1 ? y + freeHeight - cursor : area / columnWidth;
        out.push({ item: list[index + offset], x, y: cursor, width: columnWidth, height: tileHeight });
        cursor += tileHeight;
      });
      x += columnWidth;
      freeWidth -= columnWidth;
    } else {
      // Lay the row across the top.
      const rowHeight = last ? freeHeight : Math.min(freeHeight, rowArea / freeWidth);
      let cursor = x;
      row.forEach((area, offset) => {
        const tileWidth = offset === row.length - 1 ? x + freeWidth - cursor : area / rowHeight;
        out.push({ item: list[index + offset], x: cursor, y, width: tileWidth, height: rowHeight });
        cursor += tileWidth;
      });
      y += rowHeight;
      freeHeight -= rowHeight;
    }
    index = next;
  }
  return out;
}

// --- Robinhood row layout --------------------------------------------------
// The Robinhood card is not a squarified treemap: it is full-width ROWS, the
// biggest names in a tall top row, then shorter rows of smaller names (16 tiles
// = 5 / 5 / 6), each row sorted biggest-first left to right. Row height follows
// the row's total weight, tile width the tile's share of its row.

export function rowCounts(count, width) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  if (!n) return [];
  const maxPerRow = Math.max(2, Math.min(7, Math.floor((Number(width) || 0) / 50)));
  const rows = Math.min(n, Math.max(Math.ceil(n / maxPerRow), n <= 5 ? 1 : n <= 10 ? 2 : 3));
  const base = Math.floor(n / rows);
  const extra = n % rows;
  // Later (shorter) rows take the extra tiles: their names are smaller.
  return Array.from({ length: rows }, (_, index) => base + (index >= rows - extra ? 1 : 0));
}

export function rowLayout(items, rect, { minRowHeight = 30 } = {}) {
  const width = Math.max(0, Number(rect?.width) || 0);
  const height = Math.max(0, Number(rect?.height) || 0);
  const list = (Array.isArray(items) ? items : [])
    .filter((item) => Number(item?.weight) > 0)
    .sort((left, right) => right.weight - left.weight);
  if (!list.length || width <= 0 || height <= 0) return [];
  const originX = Number(rect?.x) || 0;
  const originY = Number(rect?.y) || 0;

  const rows = [];
  let cursor = 0;
  for (const size of rowCounts(list.length, width)) {
    rows.push(list.slice(cursor, cursor + size));
    cursor += size;
  }
  const sums = rows.map((row) => row.reduce((total, item) => total + item.weight, 0));
  const total = sums.reduce((a, b) => a + b, 0);
  let heights = sums.map((sum) => (height * sum) / total);
  // Lift rows below the floor (so the bottom row can still print a ticker)
  // and take the difference proportionally from the rows above the floor.
  const floor = Math.min(minRowHeight, height / rows.length);
  for (let pass = 0; pass < rows.length; pass += 1) {
    const low = heights.map((h) => h < floor - 1e-9);
    if (!low.some(Boolean)) break;
    const fixed = low.filter(Boolean).length * floor;
    const freeSum = sums.reduce((acc, sum, index) => acc + (low[index] ? 0 : sum), 0);
    heights = heights.map((h, index) => (low[index] ? floor : ((height - fixed) * sums[index]) / freeSum));
  }

  const out = [];
  let y = originY;
  rows.forEach((row, rowIndex) => {
    const rowHeight = rowIndex === rows.length - 1 ? originY + height - y : heights[rowIndex];
    let x = originX;
    row.forEach((item, index) => {
      const tileWidth = index === row.length - 1 ? originX + width - x : (width * item.weight) / sums[rowIndex];
      out.push({ item, x, y, width: tileWidth, height: rowHeight, row: rowIndex });
      x += tileWidth;
    });
    y += rowHeight;
  });
  return out;
}

// Card height for `count` tiles at `width` px. Two floors: enough area per
// tile that an average one can print its ticker (narrow dock column), and a
// Robinhood-like shape so a wide card is a block, not a one-row strip.
export function cardHeight(count, width, expanded = false) {
  const tiles = Math.max(1, Number(count) || 1);
  const span = Math.max(120, Number(width) || 0);
  const byArea = Math.ceil((tiles * 4200) / span);
  const aspect = tiles >= 10 ? 0.5 : tiles >= 6 ? 0.38 : tiles >= 3 ? 0.3 : tiles === 2 ? 0.24 : 0.18;
  const byShape = Math.round(span * aspect);
  return Math.max(72, Math.min(expanded ? 720 : 340, Math.max(byArea, byShape)));
}

// Font sizes (px) for a tile: big tiles get big bold tickers, as on the
// Robinhood card, small ones shrink down to a readable floor.
export function tileFontSizes(width, height, style = "tos") {
  if (style === "rh") {
    // Robinhood: ticker and % the same size, ticker bold, % regular.
    const symbol = Math.round(Math.max(10, Math.min(18, width / 4.4, height / 3.2)));
    return { symbol, pct: symbol };
  }
  const symbol = Math.round(Math.max(9, Math.min(20, width / 4.2, height / 2.6)));
  return { symbol, pct: Math.max(8, Math.round(symbol * 0.85)) };
}

// What a tile of this pixel size has room to print. Robinhood drops the % on
// its short bottom row (MU, AMD, ASML... print the ticker only).
export function tileLabelMode(width, height, style = "tos") {
  if (width >= 46 && height >= (style === "rh" ? 52 : 34)) return "full";
  if (width >= 30 && height >= 16) return "symbol";
  return "none";
}
