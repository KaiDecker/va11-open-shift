# Game patch source

This directory contains original patch metadata and GML source only. It must
never contain `data.win`, extracted sprites, fonts, audio, or dialogue from the
game or reference mods.

The Stage 9 patch targets only the Steam Windows baseline recorded in
`manifest.json`. A patcher must fail closed when the hash or any required
resource name differs. It must write to a temporary copy, verify the result,
and leave the installed original untouched until an explicit install step.

`apply_mod.csx` is the executable UndertaleModTool 0.9.1.2 patch source. It
adds an Extra Chapters entry using the original `blue_chapter` and
`yellow_chapter` sprites. Its position, 14-pixel expansion, chapter-label
fonts, transition through `towork_load`, and bar-room entry follow the
reference mod's `reun` / `reunstart` flow. The authenticated loopback
controller then drives the original `obj_textbox` and whitelisted character
objects. Generated text remains plain data and never enters `execute_string`
or the original command parser.

The controller accepts 1-8 validated lines per scene. It derives the line
count from the decoded list instead of assuming exactly three lines, keeps the
speaker/portrait/expression checks for every entry, and allows up to 120
seconds for private per-Agent BYOK dialogue generation before failing closed.
The first daily graph is generated in parallel with speakerless opening text.
Python and SQLite retain null speaker and portrait fields for those ambient
lines; the HTTP transport converts both fields to empty strings for the legacy
GameMaker JSON decoder. Cached scenes then play without per-line API waits.
Provider-budget exhaustion, safe graph-generation failure/retry, and a
disconnected service have distinct Chinese messages.

Stage 6 adds Jill as a player-only speaker. Python and SQLite keep her
`portrait_id` as `null`, while the HTTP transport converts only Jill's value to
an empty string for the legacy GameMaker JSON decoder. The GameMaker client
requires that empty string for Jill, strictly matches every Agent to its
original portrait, and keeps the active customer's portrait on screen while
Jill speaks from behind the bar. Jill is never added to the autonomous Agent
scheduler.

An order-bearing scene is acknowledged before `global.mixhappens` opens the
original `recipebook_bg` and mixer. The patch appends a narrow hook to the
original `mixcontrol` script for Open Shift day 1001 only. It uploads the five
raw ingredient amounts plus ice, aging, and mixed/blended preparation to the
authenticated loopback service. It does not upload or invent a success flag;
the Python rule layer independently identifies the drink and returns a
persisted reaction scene.

The original mixer still blocks an unrecognised/failed recipe before
`mixcontrol` runs. A recognised menu recipe that differs from the customer's
request is sent to `/v1/orders/resolve` and resolves as `wrong` with zero
income, so the customer reaction can play normally. If that HTTP request is
rejected, the controller preserves the order state before switching to its
error state and includes the service error code in the diagnostic text.

The controller reads the ephemeral port and token from GameMaker's local
`open-shift-runtime.ini`. A launcher creates that runtime-only file before the
game starts; it must never be committed or logged. The patch validates the
known Steam Windows `data.win` hash and all injection-point names before making
changes. Build and installation must always operate on a copy of the game.

Stage 7 persists a bounded graph of at most three customers. Each order has
exactly four draft result scenes and a merge node. The rule layer selects and
commits only the served branch, returns an authoritative `income_delta`, and
advances the original `global.cashcounter` and `global.barscore` without allowing
GameMaker to invent a payout. Opening, waiting, doorbell, and closing text has no
speaker or portrait and never enters Agent memory. While day one is played, only
day two may be prefetched.
Income uses the original recipe prices, including the 100-gil scalable big-glass
surcharge, and updates the original short-lived `scorepop_obj` amount after the
authoritative HTTP response arrives.

Stage 8 keeps the original 24 save slots and native text format. For Open
Shift day 1001, each original save finishes first and then an authenticated
`/v1/saves/pair` request creates a WAL-safe SQLite backup. Loading an Open
Shift native slot first calls `/v1/saves/restore`; only a matching native hash,
immutable snapshot hash, slot identity, and world revision may reach the
original loader. A failed overwrite restores the previous paired native save,
and a failed restore rolls the live Agent database back. Request receipts make
pair and restore retries idempotent across bridge restarts.

After the last customer, speakerless closing and settlement text records the
authoritative shift income. A persistent, bounded controller uses the original
apartment transition, then leaves Jill's tablet and news UI fully interactive.
The player reaches the original Save/Load home through its Data icon and only
then opens the 24-slot page. Choosing "go to work" before saving follows that
same original Data-icon animation instead of skipping the checkpoint. One
successful paired save closes the save UI but keeps Jill in her apartment. The
next story day begins only after the player clicks the original go-to-work
control and its work transition completes. `current_story_day`, opening
acknowledgements, selected branches, graph generation state, income history,
and the save-required recovery point all remain in the paired SQLite snapshot.

Verified toolchain:

- UndertaleModTool / UTMT CLI `0.9.1.2`
- Original Steam Windows `data.win` SHA-256:
  `f14c4443838179f633f362c6fa20ca849d479c555eb315a507b4165ffa940991`
- A patched copy was successfully compiled, loaded again, and written a second
  time. The installed game was not modified.
