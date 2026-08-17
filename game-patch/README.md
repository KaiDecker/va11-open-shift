# Game patch source

This directory contains original patch metadata and GML source only. It must
never contain `data.win`, extracted sprites, fonts, audio, or dialogue from the
game or reference mods.

The Stage 7 patch targets only the Steam Windows baseline recorded in
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

Verified toolchain:

- UndertaleModTool / UTMT CLI `0.9.1.2`
- Original Steam Windows `data.win` SHA-256:
  `f14c4443838179f633f362c6fa20ca849d479c555eb315a507b4165ffa940991`
- A patched copy was successfully compiled, loaded again, and written a second
  time. The installed game was not modified.
