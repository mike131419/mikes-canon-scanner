# Mike's Canon Scanner – how updates are released

Repo: https://github.com/mike131419/mikes-canon-scanner (public)

## Files
| File | Purpose |
|---|---|
| `mikes_canon_scanner.py` | Main window (tkinter): scan settings, jobs list, zoom/scroll preview, edit panel, Help > Check for updates |
| `pages.py` | `Page` (original scan + list of edits: rotate / crop / scale, re-rendered from original), `Job`, merge, save PDF/JPG/PNG |
| `escl.py` | Network scanning over eSCL/AirScan (printer at 192.168.0.82), discovery on the /24 |
| `wia_backend.py` | Fallback through the Windows scanner driver (WIA, pywin32) |
| `updater.py` | Checks GitHub `releases/latest`, downloads `*Setup.exe`, runs it with `/SILENT` |
| `version.py` | `__version__` – the single source of the version number |
| `installer/setup.iss` | Inno Setup script (per-user install, Start menu + desktop shortcut, auto-relaunch after silent update) |
| `.github/workflows/build.yml` | On tag `vX.Y.Z`: tests → PyInstaller → `--selftest` of the .exe → Inno Setup → GitHub Release |
| `RELEASE_NOTES.md` | Text of the next release; shown to the user in the in-app update dialog |

## Releasing a new version
1. Make the code change; run `python tests/test_pages.py`.
2. Bump `__version__` in `version.py` (e.g. 1.0.0 → 1.1.0).
3. Replace `RELEASE_NOTES.md` with a short "what's new" list.
4. Commit, then tag and push:
   ```
   git tag v1.1.0
   git push origin main v1.1.0
   ```
   The tag must equal `v` + `version.py`, or the build stops.
5. Watch the "Build installer" Action. When it finishes, the release has `MikesCanonScanner-1.1.0-Setup.exe`.
6. Installed copies show an orange **Update to 1.1.0** button (checked 3 s after start) or via Help > Check for updates.

User settings live in `%USERPROFILE%\.mikes_canon_scanner.json` and survive updates.
