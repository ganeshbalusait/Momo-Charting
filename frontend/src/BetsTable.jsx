import { betsTableRows, shortMoney } from "./leapOi.js";

// "Which strike is that?" (2026-11-23, "$20 million went to calls which strike is that?" then "Above table I don't
// see in our app, can we do it?"): the contracts that took the most money today on each side, with their share of that
// side. Shown in the ticker's simple-words card and on each LEAP OI Scanner ticker card.
function SideTable({ rows, side, total }) {
  const lines = betsTableRows(rows, side);
  if (!lines.length) return null;
  const word = side === "puts" ? "Puts" : "Calls";
  return (
    <div className="bets-table-side" data-testid="bets-table" data-side={side}>
      <small className="bets-table-title">
        Which {word} took the money{total ? ` (of ${shortMoney(total)})` : ""}
      </small>
      <table className="bets-table">
        <thead>
          <tr><th>Contract</th><th>Ends</th><th>Traded</th><th>Money</th><th>Share</th><th>Last trade</th></tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.key} data-testid="bets-table-row">
              <td className={line.tone}><b>{line.contract}</b></td>
              <td>{line.ends}</td>
              <td>{line.traded}</td>
              <td>{line.money}</td>
              <td>{line.share}</td>
              <td>{line.lastTrade}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function BetsTable({ calls, puts, callTotal, putTotal }) {
  if (!(calls?.length || puts?.length)) return null;
  return (
    <div className="bets-tables">
      <SideTable rows={calls} side="calls" total={callTotal} />
      <SideTable rows={puts} side="puts" total={putTotal} />
      <small className="bets-table-note">Top 5 contracts on each side by today's money. Last trade: Buyer = paid the asking price, Seller = took the bid.</small>
    </div>
  );
}
