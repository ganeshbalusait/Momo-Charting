import { memo, useEffect, useMemo, useState } from "react";

import {
  parseReleases,
  versionLabel,
  formatStamp,
  newestAt,
  writeSeen,
} from "./releaseNotes.js";
import { currentEntryScript, fetchLatestEntryScript, markReleaseNotesOnNextLoad } from "./appVersion.js";

// Why a fetched file and not a constant compiled into the bundle: the point of
// this page is to be checked AFTER the fact ("the chain went blank at 3pm -
// what shipped before that?"), and a file can be read on disk, diffed in git,
// and shipped by a frontend build alone.
const RELEASE_NOTES_URL = "/release-notes.json";

function ReleaseNotesPanel() {
  const [state, setState] = useState({ status: "loading", data: null, error: "" });
  // null = not yet known. Never render a claim about the installed build until
  // the check has actually answered.
  const [updateAvailable, setUpdateAvailable] = useState(null);

  useEffect(() => {
    let cancelled = false;
    // cache: no-store and a buster because the notes file is served next to
    // the bundle, not inside it - a cached copy would show yesterday's list
    // while reading as authoritative, which is worse than showing none.
    fetch(`${RELEASE_NOTES_URL}?t=${Date.now()}`, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => { if (!cancelled) setState({ status: "ready", data, error: "" }); })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", data: null, error: String(error?.message || error) });
      });
    return () => { cancelled = true; };
  }, []);

  const releases = useMemo(() => parseReleases(state.data), [state.data]);

  // Opening the page IS reading it. Marking on mount rather than on scroll
  // keeps the dot honest without pretending to know what he actually read.
  useEffect(() => {
    if (releases.length === 0) return;
    const stamp = newestAt(releases);
    if (stamp && typeof window !== "undefined") writeSeen(window.localStorage, stamp);
  }, [releases]);

  // "Currently Installed" has to be earned. The notes file is served beside
  // the bundle, not inside it, so it can legitimately be newer than the code
  // running right now - which is exactly the moment the update banner fires.
  useEffect(() => {
    let cancelled = false;
    if (typeof window === "undefined") return undefined;
    const current = currentEntryScript(window.document);
    if (!current) return undefined;
    fetchLatestEntryScript(window.fetch.bind(window)).then((latest) => {
      // Either side unknown teaches us nothing - an expired Cloudflare Access
      // session answers with a login page that has no module script.
      if (!cancelled && latest) setUpdateAvailable(latest !== current);
    });
    return () => { cancelled = true; };
  }, []);

  const newest = releases[0] || null;
  const version = newest ? versionLabel(newest, releases) : null;

  const applyUpdate = () => {
    if (typeof window === "undefined") return;
    markReleaseNotesOnNextLoad(window.sessionStorage);
    window.location.reload();
  };

  return (
    <div className="release-notes">
      <h1 className="release-notes-title">Release Notes</h1>

      <div className="release-notes-version-block">
        <div className="release-notes-version">{version || "—"}</div>
        {updateAvailable === true ? (
          <button type="button" className="release-notes-status-update" onClick={applyUpdate}>
            Update available &ndash; tap to install
          </button>
        ) : (
          <div className="release-notes-status-installed">
            {/*
              Three states, not two. "Currently Installed" is a CLAIM about the
              running bundle, so it is only made when the check actually
              answered. When it cannot answer - offline, an expired Cloudflare
              Access session, or the dev server, where Vite injects its own
              module script and the comparison is meaningless - the honest line
              is about the notes rather than the install. It previously read
              "Checking…" in that case, which on :5173 never resolved and read
              as a hung page.
            */}
            {updateAvailable === false ? "Currently Installed" : "Latest release"}
          </div>
        )}
      </div>

      {state.status === "loading" && <p className="release-notes-note">Loading&hellip;</p>}
      {state.status === "error" && (
        <p className="release-notes-note release-notes-note-error">
          Could not load the release notes ({state.error}). They live in the app at{" "}
          <code>frontend/public/release-notes.json</code>.
        </p>
      )}
      {state.status === "ready" && releases.length === 0 && (
        <p className="release-notes-note">No releases recorded yet.</p>
      )}

      {releases.map((release, index) => (
        <section className="release-entry" key={`${release.at || "undated"}-${index}`}>
          {release.at && <div className="release-entry-stamp">{formatStamp(release.at)}</div>}
          {release.heading && <h2 className="release-entry-heading">{release.heading}</h2>}
          {release.subheading && <p className="release-entry-sub">{release.subheading}</p>}
          {release.items.length > 0 && (
            <ul className="release-entry-items">
              {release.items.map((item, i) => <li key={i}>{item}</li>)}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

// Static once fetched: it must never re-render on a dashboard poll.
export default memo(ReleaseNotesPanel);
