// Settings must not lose to a cache (2026-10-08, his phone: "Unable to save D
// settings"). The MomX board copies (momx.board.*) had grown to ~4.6 MB of the
// ~5 MB browser storage, so every settings write on the phone threw. A board
// copy is only a warm-start convenience - the next fetch rebuilds it - while a
// settings save is the trader's own work. So a write that hits the quota drops
// the board copies and tries once more.

import { MOMX_BOARD_CACHE_PREFIX } from "./momxCells.js";

function store(storage) {
  if (storage) return storage;
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function dropBoardCaches(storage) {
  const target = store(storage);
  if (!target) return 0;
  const keys = [];
  try {
    for (let index = 0; index < target.length; index += 1) {
      const key = target.key(index);
      if (key && key.startsWith(MOMX_BOARD_CACHE_PREFIX)) keys.push(key);
    }
    keys.forEach((key) => target.removeItem(key));
  } catch {
    return 0;
  }
  return keys.length;
}

/** setItem that never throws; true when the value was stored. */
export function setItemMakingRoom(key, value, storage) {
  const target = store(storage);
  if (!target) return false;
  try {
    target.setItem(key, value);
    return true;
  } catch {
    if (!dropBoardCaches(target)) return false;
    try {
      target.setItem(key, value);
      return true;
    } catch {
      return false;
    }
  }
}
