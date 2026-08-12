# Game patch source

This directory contains original patch metadata and GML source only. It must
never contain `data.win`, extracted sprites, fonts, audio, or dialogue from the
game or reference mods.

The Stage 3 patch targets only the Steam Windows baseline recorded in
`manifest.json`. A patcher must fail closed when the hash or any required
resource name differs. It must write to a temporary copy, verify the result,
and leave the installed original untouched until an explicit install step.

`apply_mod.csx` is the executable UndertaleModTool 0.9.1.2 patch source. It
adds an Extra Chapters entry using the original `blue_chapter` and
`yellow_chapter` sprites, an authenticated loopback HTTP controller, and a safe
text renderer without copying binary assets. Its position, 14-pixel expansion,
and two-step interaction follow the reference mod's `reun` / `reunstart`
objects. Generated text remains plain
data and never enters `execute_string` or the original command parser.

The controller reads the ephemeral port and token from GameMaker's local
`open-shift-runtime.ini`. A launcher creates that runtime-only file before the
game starts; it must never be committed or logged. The patch validates the
known Steam Windows `data.win` hash and all injection-point names before making
changes. Build and installation must always operate on a copy of the game.

Verified toolchain:

- UndertaleModTool / UTMT CLI `0.9.1.2`
- Original Steam Windows `data.win` SHA-256:
  `f14c4443838179f633f362c6fa20ca849d479c555eb315a507b4165ffa940991`
- A patched copy was successfully compiled, loaded again, and written a second
  time. The installed game was not modified.
