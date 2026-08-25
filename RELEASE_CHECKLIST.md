# Open Shift Release Checklist

## Build inputs

- [ ] Worktree is clean and release commit is identified.
- [ ] `game-patch/manifest.json` contains the supported Steam Windows hashes.
- [ ] Steam `data.win` and executable hashes match the manifest.
- [ ] No `data.win`, original assets, SQLite databases, API keys, archives, or
      `reference-local` files are staged.
- [ ] Windows x64 RC ZIP has an explicit prerelease version and SHA-256.
- [ ] WebView2 SDK build inputs are resolved explicitly or by supported discovery.

## Automated verification

- [ ] `python -m unittest discover -s tests` passes.
- [ ] The 30-day and 365-day deterministic soak tests pass without rejected
      actions or provider errors.
- [ ] UTMT 0.9.1.2 compiles `game-patch/apply_mod.csx` from the verified
      original into a temporary output.
- [ ] `verify-patch-output` accepts the temporary output and committed GML
      source tree.
- [ ] Two UTMT load/write verification passes produce the same SHA-256.
- [ ] The Steam original SHA-256 is unchanged after all build steps.

## Installation and recovery

- [ ] `install-patch` targets an isolated game copy and creates a backup plus
      install record before replacement.
- [ ] Launch, bridge health, one complete shift, paired save, restart, and load
      are verified on the installed copy.
- [ ] A mismatched native/SQLite pair is rejected without changing live state.
- [ ] `uninstall-patch` restores the backup when the installed output matches.
- [ ] Uninstall refuses a file modified after installation.
- [ ] A clean extraction installs without Python or a separately downloaded UTMT.
- [ ] A previous Open Shift install upgrades by patch fingerprint and keeps its
      database, paired saves, API credential, and native save directory.
- [ ] Re-running the same RC recognizes the verified isolated patch without rebuilding.
- [ ] WebView2 Runtime absence produces a Chinese actionable diagnostic.
- [ ] Steam libraries outside the system drive are discovered from `libraryfolders.vdf`.
- [ ] All mutating GUI controls are disabled while install or launch is active.
- [ ] GUI uninstall requires confirmation and preserves saves by default.
- [ ] Window, taskbar, executable, and desktop shortcut use the current OPEN SHIFT icon.

## Secrets and configuration

- [ ] Runtime TOML passes `validate-config` and contains only an API key
      environment-variable name, never the key value.
- [ ] Provider timeout, model, protocol, thinking mode, call budget, and bounded
      prefetch depth match the intended release defaults.
- [ ] Logs, SQLite snapshots, paired-save manifests, install records, and test
      output contain no API keys, bridge tokens, or full private prompts.

## Manual game acceptance

### Stage 19 known issues (not release-ready)

The rc.20 real-game run failed at the first scene acknowledgement even though
`timing.log` recorded `POST /v1/scenes/ack -> 200`. The cause was a client-side
GameMaker ACK validator rejecting the response after JSON field coercion; the
server had already accepted it. The same run also showed GameMaker client
diagnostic requests returning HTTP 400 because real runtime values included
`phase=client`, room-transition states, integer-valued floats, uninitialized
`null` cursors, and engine-sized HTTP handles that were not covered by the
validator. rc.21 includes the narrow ACK compatibility fix and bounded,
GameMaker-compatible diagnostic normalization. Real-game acceptance of rc.21
is still pending.

The rc.21 real-game run still displayed `phase: ack, HTTP -1 / transport 0`
immediately after entering the bar. The bridge log showed `/v1/scenes/ack ->
200`, so this was not a server rejection. GameMaker had delivered a callback
without a usable `http_status` and with a body shape that was neither empty nor
the canonical accepted envelope. rc.22 narrows the compatibility rule to ACK
callbacks only: transport success plus missing/negative HTTP status succeeds
unless the body contains an explicit error envelope. Ordinary scene and order
responses remain strict. The rc.22 package is pending fresh real-game
acceptance.

Stage 19 now uses the original `obj_textbox` lifecycle and original break/save
UI. The break hand-off is ordered as: dynamic break scene finishes in the bar,
Open Shift sends and waits for `/v1/scenes/ack`, then the successful ACK enters
`break_time`; vanilla `break_changer` calls `break_return()` and creates the
save UI; returning to the bar leaves `cur_client`/`cur_stage` untouched and
queues the next `/v1/scenes/jobs` request through the bridge. Open Shift's
`cur_day >= 1001` is outside the original `break_return()` switch, so the
bridge remains the sole dynamic-text owner after the vanilla save page closes.
The bridge does not send a second ACK after return and does not use a `-99`
cursor sentinel.

The rc.22 real-game run still showed `phase: ack, HTTP -1 / transport 0`.
Although the server had accepted the ACK, this exposed the wrong ownership
boundary: the client entered the native room before the ACK had completed and
then tried to resume by blocking the vanilla cursor. The fix is to make ACK
completion the only room-entry gate, preserve vanilla save UI ownership, and
queue the next bridge scene after return. A fresh real-game package must
verify this exact sequence before Stage 19 can be called complete.

- The rc.18 acceptance run still returned from the original four-portrait
  break/save UI with an empty textbox or `NO SIGNAL`; the third customer's
  dialogue/order state did not resume. The root cause is confirmed as a stale
  HTTP request id being reused across the break return, so the client could
  receive an old acknowledgement instead of the next scene. The bridge now
  rejects stale ids and emits GameMaker `client_event` timing diagnostics;
  real-game acceptance of the fix is still pending.
- After a provider `...` wait finishes, a continuing speaker such as Alma can
  lose its portrait. The root cause is confirmed as a race between the
  wait-box `HIDEALL` fade and creation of the replacement textbox: the fade
  could clear the active portrait after the replacement line had restored it.
  The bridge now orders the fade and portrait restoration safely; real-game
  acceptance of the fix is still pending.

These are acceptance blockers. Do not describe Stage 19 as a complete vanilla
flow until the next stage verifies portrait continuity, break/save return,
third-customer continuation, and restart/load recovery in a real game process.

The latest build also records GameMaker-side `client_event` timing entries for
scene requests, callbacks, textbox replacement, break return, and resume
gates. These diagnostics are intended to identify the exact client state and
request id when a real-game acceptance run diverges.

- [ ] Jill speaks without a portrait while the active customer remains visible.
- [ ] Exact, acceptable, wrong, and special drink branches resolve correctly.
- [ ] Served drinks use the original recipe prices (Moonblast is 180), scalable
      doubled drinks add 100, and wrong drinks add 0.
- [ ] The short-lived top-right score popup matches the authoritative drink
      income instead of displaying 0.
- [ ] Shift income reaches Jill's wallet exactly once.
- [ ] The tablet shows `O.S. DAY N` and the original 24-slot save UI remains usable.
- [ ] A paired save enters the next business day and restores after restart.
- [ ] Provider failure presents a retryable diagnostic instead of hiding forever.

## Publication

- [ ] Release notes describe compatible hashes and supported platform.
- [ ] Installation, backup, configuration, launch, save recovery, uninstall, and
      troubleshooting commands are current.
- [ ] Package contains source, metadata, and scripts only; it contains no
      copyrighted game data or generated `data.win`.
- [ ] Player release package contains `OpenShift.exe`, `OpenShiftSetup.exe`, and
      bundled UTMT CLI, and its
      manifest has no `data.win`, game executable, SQLite, `reference-local`,
      or API key.
- [ ] A fresh Windows user can install from the package without Python, enter a
      DPAPI-protected DeepSeek key, launch from the desktop shortcut, complete
      two business days, and uninstall without changing Steam `data.win`.
- [ ] The Steam original SHA-256 before install, after upgrade, after a complete
      DeepSeek-backed shift, and after uninstall is identical.
