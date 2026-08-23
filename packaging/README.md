# OPEN SHIFT 玩家发行包

This package contains the OPEN SHIFT bridge, patch source and safe launcher
scripts. It does **not** contain VA-11 HALL-A's executable, `data.win`, Steam
assets, saves, databases, runtime INI files or API keys.

The installer takes a user-owned Steam game directory, verifies its original
`data.win` hash, creates an isolated game copy, and applies the patch there.
The Steam installation is never used as the installation destination.

Requirements for the source/maintainer workflow:

- Windows PowerShell 5+;
- Python 3.11+;
- UndertaleModTool CLI 0.9.1.2;
- a user-owned VA-11 HALL-A installation;
- a DeepSeek API key supplied only through `OPEN_SHIFT_API_KEY`.

For the final player package, double-click `OpenShiftSetup.exe`. Its WebView2-based
Windows GUI detects the Steam library, validates the original hash, creates or repairs the
patched isolated copy, stores the DeepSeek key with current-user DPAPI, and
creates a desktop `Open Shift` shortcut. The same GUI prepares the next day,
starts the game, opens diagnostics, switches DeepSeek Thinking with validated
TOML persistence, and safely uninstalls the isolated copy.

The public project name is **OPEN SHIFT**. Community-facing posts are maintained
outside the player package and may be adapted for local community rules.

The final package contains `OpenShift.exe`, `OpenShiftSetup.exe` with the Open Shift icon,
the WebView2 host libraries, `OpenShift.ico`, and UTMT CLI. The lower-level
`install-isolated-copy.ps1` and `launch-open-shift.ps1` scripts remain available
for maintainer and acceptance workflows.

For a strict real-DeepSeek acceptance run, use
`launch-deepseek-acceptance.ps1`. It reads the API key with hidden input,
probes DeepSeek, creates a new timestamped database, and generates the first
day before opening the copied game. Provider failures stop the launch instead
of silently switching to local dialogue.

Players can switch the DeepSeek generation mode in the GUI after installation:
快速 keeps ordinary dialogue fast, 平衡 enables Thinking only for world
decisions, and 深度 enables it for every generation. Maintainers can pass
`-Thinking enabled` or `-Thinking balanced` to the acceptance script. Day entry prepares only
a local deterministic skeleton; provider calls are made on demand for the scene
and drink branch the player actually reaches. The default remains `disabled`
because thinking increases generation time and token use.
Installed launchers also write secret-free JSONL timing records to
`timing.log`, including each provider request, thinking mode, elapsed
milliseconds, and the total daily graph preparation time. This makes it
possible to compare thinking with non-thinking before changing the generation
strategy.

The bridge also exposes a non-blocking scene-job protocol for the newer client:
`POST /v1/scenes/jobs` returns a `job_id` immediately, and
`GET /v1/scenes/jobs/<job_id>` reports `queued`, `running`, `ready`, or
`failed`, together with UTC timestamps and elapsed milliseconds. The legacy
`POST /v1/scenes/open` endpoint remains available while the GameMaker client
migrates to polling, so existing acceptance builds continue to work.
Pass `-Database <path>` to resume an existing world; omit it to create a new
timestamped acceptance database.
