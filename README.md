# tableau-migrate

A small command-line tool that moves a Tableau workbook from one Tableau Server
(or site) to another — e.g. **TST → PRD** — by repointing its published data
sources, and optionally renaming them (`Apple1` → `Apple_1`).

- Works with `.twb` and `.twbx` (packaged workbooks are repackaged with extracts/images intact)
- Interactive prompts — no flags to remember
- Never modifies your original file; writes `MyWorkbook_prd.twbx` next to it
- Validates the result before saving; writes nothing if something looks wrong
- Standard library only — no `pip install`

## Why

A workbook connected to a published data source stores a hard-coded pointer to
one specific server, site and data source content URL. Publishing it to another
server leaves it pointing at the old one. This tool rewrites those pointers.

## Requirements

- Python 3.8+
- The data sources must **already be published** on the target server.
  This tool only repoints the workbook; it doesn't copy data sources.

## Setup (one time)

Open `tableau_migrate.py` and fill in the `SERVERS` block at the top:

```python
SERVERS = {
    "tst": {"host": "tableau-tst.company.com", "site": "TstSite", "protocol": "https", "port": "443"},
    "prd": {"host": "tableau-prd.company.com", "site": "PrdSite", "protocol": "https", "port": "443"},
}
```

Find these in your browser's address bar on the server:

```
https://tableau-prd.company.com/#/site/Analytics/views/...
        └──────── host ───────┘        └ site ┘
```

No `/site/` in the URL means the Default site — use `"site": ""`.

## Usage

```
python tableau_migrate.py
```

or, on Windows, double-click `migrate.bat` — or drag a workbook onto it.

Then:

1. Drag your `.twb` / `.twbx` into the window and press Enter
2. Pick a target: `tst`, `prd`, a custom server (paste any URL from it), or keep the current server
3. Optionally rename data sources — pick a number, type the new name
4. Confirm the summary

Then open the new file in Tableau Desktop **while signed in to the target
server**, check the sheets load, and publish.

### Renaming data sources

Use the name exactly as it appears at the **end of the data source's URL** on
the target server (its content URL), e.g. `.../datasources/Apple_1` → `Apple_1`.
That can differ from the display name.

## Notes

- Project/folder doesn't matter — workbooks don't store which project a data source is in.
  Viewers do need permission on the data sources in the target project.
- Only published (server) data sources are handled. Embedded connections
  (direct to a database or Excel file) are left untouched.
- If Desktop says it can't find a data source, compare the name you used with
  the end of that data source's URL on the server.
