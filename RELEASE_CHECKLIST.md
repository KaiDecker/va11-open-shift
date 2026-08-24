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

Stage 19 currently reuses the original `obj_textbox` lifecycle and the original
break/save UI, but it does not yet hand the complete flow back to the original
controller chain. Open Shift still owns the dynamic dialogue queue, portrait
state, bridge callbacks, scene acknowledgement, and story cursor.

- After closing the original four-portrait break/save UI, the bar can return
  with an empty textbox or `NO SIGNAL`; the third customer's dialogue/order
  state may not resume. Suspected causes include return/ACK ordering and a
  mismatch between `cur_client`/`cur_stage` and the Open Shift story cursor;
  the single root cause is not confirmed.
- After a provider `...` wait finishes, a continuing speaker such as Alma can
  lose its portrait. Suspected causes include wait-box `HIDEALL`/`SHOW` effects,
  bridge portrait state diverging from native instances, or the next native
  textbox load clearing the customer object; the single root cause is not
  confirmed.

These are acceptance blockers. Do not describe Stage 19 as a complete vanilla
flow until the next stage verifies portrait continuity, break/save return,
third-customer continuation, and restart/load recovery in a real game process.

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
