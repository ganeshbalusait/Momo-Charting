import React from "react";

import { clearRememberedView } from "./appCrashRecovery";

// Without a boundary, any throw during render unmounts the whole tree and the
// trader sees a white screen with no indication of what happened - on a phone,
// with no devtools. This turns that into a readable message plus two ways out.
export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Keep the last crash reachable from the console for a remote diagnosis.
    if (typeof window !== "undefined") {
      window.__lastAppCrash = { message: String(error?.message || error), stack: info?.componentStack };
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="app-crash-screen" role="alert">
        <h1>The workspace stopped rendering</h1>
        <p>{String(error?.message || error)}</p>
        <div className="app-crash-actions">
          <button
            type="button"
            onClick={() => {
              clearRememberedView(typeof window !== "undefined" ? window.localStorage : null);
              if (typeof window !== "undefined") window.location.reload();
            }}
          >
            Reset to the default page
          </button>
          <button
            type="button"
            onClick={() => { if (typeof window !== "undefined") window.location.reload(); }}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
