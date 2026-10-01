// Temporary passwords for account creation and admin resets.
//
// Generated rather than typed: the administrator never sees it twice (the
// server hashes it on arrival), it must satisfy auth_service._validate_password
// on the first try, and it gets read down a phone often enough that ambiguous
// glyphs are a real cost.

const LOWER = "abcdefghijkmnpqrstuvwxyz"; // no l
const UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"; // no I, no O
const DIGITS = "23456789"; // no 0, no 1
const ALL = LOWER + UPPER + DIGITS;

const LENGTH = 14;

export function generateTemporaryPassword() {
  const draws = new Uint32Array(LENGTH);
  globalThis.crypto.getRandomValues(draws);

  // One of each required class up front guarantees the server's rules are met
  // however the remaining draws fall; positions are then shuffled so those
  // classes do not always sit in the same three slots.
  const characters = [
    LOWER[draws[0] % LOWER.length],
    UPPER[draws[1] % UPPER.length],
    DIGITS[draws[2] % DIGITS.length],
  ];
  for (let index = 3; index < LENGTH; index += 1) {
    characters.push(ALL[draws[index] % ALL.length]);
  }

  const order = new Uint32Array(LENGTH);
  globalThis.crypto.getRandomValues(order);
  for (let index = LENGTH - 1; index > 0; index -= 1) {
    const swap = order[index] % (index + 1);
    [characters[index], characters[swap]] = [characters[swap], characters[index]];
  }
  return characters.join("");
}
