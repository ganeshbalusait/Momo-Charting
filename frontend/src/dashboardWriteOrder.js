// Two things write the dashboard state: the 5-second poll, and every mutation
// the trader performs (add/edit/delete a ticker, start a job). They were
// completely uncoordinated, and the poll is the one that usually lands last.
//
// The bug that produced this: tap Delete on a watchlist ticker while a poll is
// already in flight. The server deletes it and returns the new 357-symbol list,
// which renders. Then the poll - whose snapshot was taken BEFORE the delete -
// responds with the old 358-symbol list and overwrites it. The ticker comes
// back, and Delete looks broken even though the server did exactly what it was
// asked. The window is up to 5 seconds wide on every cycle.
//
// A poll response is only safe to apply if no mutation has committed since that
// poll was issued. This is the same shape as the sequence guard
// refreshOiFinderNews already uses to drop superseded news responses.
export function createDashboardWriteOrder() {
  let committedMutations = 0;
  return {
    // Call when a poll request is ISSUED; hand the token back on arrival.
    beginPoll() {
      return committedMutations;
    },
    // Call when a mutation's response has been applied to state.
    commitMutation() {
      committedMutations += 1;
      return committedMutations;
    },
    // A poll may only write if the world has not moved under it.
    shouldApplyPoll(token) {
      return token === committedMutations;
    },
  };
}
