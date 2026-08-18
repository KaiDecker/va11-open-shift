# Open Shift Release Checklist

## Build inputs

- [ ] Worktree is clean and release commit is identified.
- [ ] `game-patch/manifest.json` contains the supported Steam Windows hashes.
- [ ] Steam `data.win` and executable hashes match the manifest.
- [ ] No `data.win`, original assets, SQLite databases, API keys, archives, or
      `reference-local` files are staged.

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

## Secrets and configuration

- [ ] Runtime TOML passes `validate-config` and contains only an API key
      environment-variable name, never the key value.
- [ ] Provider timeout, model, protocol, thinking mode, call budget, and bounded
      prefetch depth match the intended release defaults.
- [ ] Logs, SQLite snapshots, paired-save manifests, install records, and test
      output contain no API keys, bridge tokens, or full private prompts.

## Manual game acceptance

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
