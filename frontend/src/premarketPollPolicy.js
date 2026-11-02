// Heavy premarket analysis belongs to the visible Premarket workspace.
export function premarketPollEnabled(activeView, popoutMode) {
  return !popoutMode && activeView === "Premarket Scanner";
}
