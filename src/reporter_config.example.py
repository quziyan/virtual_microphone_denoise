"""Template for reporter_config.py (committed). Copy to reporter_config.py and
fill in your Feishu app credentials + Bitable target. reporter_config.py itself
is gitignored so the secret never enters version control.

    cp src/reporter_config.example.py src/reporter_config.py
    # then edit src/reporter_config.py with real values

The app credentials are an internal Feishu app (自建应用) that must be granted
`wiki:wiki:readonly` (to resolve the wiki node) and `bitable:app` /
`base:record:create`, and added to the target wiki/Bitable.
"""

from __future__ import annotations

APP_ID = "cli_xxxxxxxxxxxxxxxx"
APP_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# From the Bitable URL .../wiki/<WIKI_NODE_TOKEN>?table=<TABLE_ID>
WIKI_NODE_TOKEN = "xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TABLE_ID = "tblxxxxxxxxxxxxxx"
