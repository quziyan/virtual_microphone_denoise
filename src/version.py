"""Single source of truth for the app version.

Both the running app (update checks) and the build scripts read this. To cut a
release, bump __version__ here — packaging/build_pkg.sh and the PyInstaller spec
pick it up automatically, so the .pkg name, Info.plist, and the in-app version
all stay in lockstep.
"""

from __future__ import annotations

__version__ = "1.0.2"
