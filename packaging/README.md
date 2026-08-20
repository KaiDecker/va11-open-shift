# Open Shift Mod package

This package contains the Open Shift bridge, patch source and safe launcher
scripts. It does **not** contain VA-11 HALL-A's executable, `data.win`, Steam
assets, saves, databases, runtime INI files or API keys.

The installer takes a user-owned Steam game directory, verifies its original
`data.win` hash, creates an isolated game copy, and applies the patch there.
The Steam installation is never used as the installation destination.

Requirements:

- Windows PowerShell 5+;
- Python 3.11+;
- UndertaleModTool CLI 0.9.1.2;
- a user-owned VA-11 HALL-A installation;
- a DeepSeek API key supplied only through `OPEN_SHIFT_API_KEY`.

Run `install-isolated-copy.ps1` first, then use `launch-open-shift.ps1` to
start the copied game and local bridge.

For a strict real-DeepSeek acceptance run, use
`launch-deepseek-acceptance.ps1`. It reads the API key with hidden input,
probes DeepSeek, creates a new timestamped database, and generates the first
day before opening the copied game. Provider failures stop the launch instead
of silently switching to local dialogue.

Pass `-Thinking enabled` to test DeepSeek thinking while keeping whole-day
generation before gameplay. The default remains `disabled` because thinking
increases generation time and token use for every candidate dialogue line.
Pass `-Database <path>` to resume an existing world; omit it to create a new
timestamped acceptance database.
