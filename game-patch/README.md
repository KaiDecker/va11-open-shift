# Game patch source

This directory contains original patch metadata and GML source only. It must
never contain `data.win`, extracted sprites, fonts, audio, or dialogue from the
game or reference mods.

The Stage 3 patch targets only the Steam Windows baseline recorded in
`manifest.json`. A patcher must fail closed when the hash or any required
resource name differs. It must write to a temporary copy, verify the result,
and leave the installed original untouched until an explicit install step.

The current GML files define the safe-text boundary and controller state. They
are not yet an executable patch: the UndertaleModTool version and import API
must be verified before `apply_mod.csx` is added. Generated text must remain
plain data and must never enter `execute_string` or the original command parser.
