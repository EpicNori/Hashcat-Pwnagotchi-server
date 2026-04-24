# Next Steps For Dev

Use these prompts for the next AI pass. They are written to be copied directly into a chat.

## Prompts

1. `Check the live Windows install under C:\ProgramData\HashcatWPAServer and verify that crackserver start, stop, update, and status all work after the latest launcher and updater fixes. If anything still fails, patch the repo scripts and the live install copy if possible.`

2. `Review update.ps1 for any remaining file-lock or rollback edge cases. Make sure the updater does not fail when logs or PID files are still open, and keep the install recoverable if a copy step fails.`

3. `Audit the Windows launcher scripts in windows\run_server.ps1, windows\crackserver.ps1, windows\update_app.ps1, and windows\crackserver.cmd. Remove duplicated logic if possible and keep the command names consistent and intuitive.`

4. `Search the repo for any other orphaned modules, templates, assets, or packaging leftovers. Remove only the files that are truly unused and keep a short explanation of why each deletion is safe.`

5. `Add tests or lightweight verification for the new Rainbow workload mode and the capture conversion fallback so future changes do not break them again.`

6. `Review the installer and updater behavior on both Windows and Linux. Document which parts are shared application code and which parts are deployment-only scripts so future changes do not accidentally touch the wrong layer.`

7. `If the app still references the deleted app/cluster.py path anywhere, remove the dead import or call site and confirm the app still starts cleanly.`

## Suggested Order

1. Fix live Windows behavior first.
2. Clean up any remaining dead code.
3. Add tests for the recently touched paths.
4. Update docs only after the behavior is stable.
