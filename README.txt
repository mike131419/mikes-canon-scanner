MIKE'S CANON SCANNER - for the Canon imageCLASS MF635Cx
=======================================================

START
  Double-click "Start Mike's Canon Scanner.bat".
  First run installs two small Python packages (Pillow, pywin32).
  Needs Python 3.9+ for Windows (python.org).

CONNECTING
  Network (eSCL)        - talks straight to the printer at 192.168.0.82. No driver needed.
  Windows driver (WIA)  - uses the scanner Windows already has. Use if Network fails.
  "Find on network"     - searches for the printer if its IP changes.

SCANNING
  Flatbed or document feeder (+ double-sided), Color / Grayscale / B&W,
  paper size A4 / Letter / Legal / A5 / whole glass.
  Resolution: 150 / 300 / 600 DPI are the scanner's true optical settings.
  "1200 (enhanced)" scans at 600 and enlarges by software (the same thing Canon's
  own driver does above 600). More pixels, but no extra real detail. Files get big.

SCAN JOBS
  Every press of Scan creates a new job in the "Scan jobs" list.
  Tick "Add next scan to the selected job" to add pages to an existing job instead.
  Merge: Ctrl+click two or more jobs, then "Merge selected jobs".
  Double-click a job to rename it. Its name becomes the file name when saving.

PREVIEW
  Mouse wheel scrolls up/down through all pages of the job.
  Ctrl + wheel zooms (around the mouse). Drag with the mouse to pan.
  "100% (actual pixels)" / "200%" to check sharpness. Fit width / Fit page.

EDITING (right-hand panel, works on the selected page)
  Rotate        Left 90 / Right 90 / 180.
  Straighten    slider or type any angle (+ = clockwise); a grid helps you line it up.
                Press "Apply angle".
  Crop          tick "Crop tool", drag a box on the page, press "Apply crop".
  Enlarge/shrink  type a % (e.g. 50 or 150), press Apply. Shows new pixels and print size.
  Tick "Apply ... to all pages in this job" to do rotate/angle/resize to every page.
  Undo (Ctrl+Z) and "Reset page to original scan" - the original is always kept,
  so edits never lose quality.

SAVING
  PDF (one file per job) or JPG / PNG (one file per page).
  "Save selected job(s)" or "Save all jobs" (each job its own file).
  Default folder: Documents\Scans. Settings are remembered.

TROUBLESHOOTING
  "Could not reach the scanner" - wake the printer (touch its screen), press Connect.
  Still failing - switch Method to "Windows driver (WIA)".
