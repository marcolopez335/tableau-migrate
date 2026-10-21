#!/usr/bin/env python3
"""
tableau_migrate.py - Repoint a Tableau workbook's published data sources to a
different Tableau Server / site, and optionally rename them.

Usage:
    python tableau_migrate.py                    (asks for everything)
    python tableau_migrate.py "My Workbook.twbx" (asks for the rest)

Your original file is NEVER modified. The result is saved next to it, e.g.
    My Workbook.twbx  ->  My Workbook_prd.twbx

Standard library only - nothing to pip install.
"""

import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as _xml_escape, unescape as _xml_unescape

# =============================================================================
#  EDIT THESE FOR YOUR ENVIRONMENT
#  host     : server hostname only (no https://, no trailing slash)
#  site     : the site's content URL - the part after "/site/" in the browser
#             URL. Use "" (empty) if you're on the Default site.
#  protocol : "https" (almost always) or "http"
#  port     : "443" for https, "80" for http
# =============================================================================
SERVERS = {
    "tst": {
        "host": "tableau-tst.yourcompany.com",
        "site": "YourTstSite",
        "protocol": "https",
        "port": "443",
    },
    "prd": {
        "host": "tableau-prd.yourcompany.com",
        "site": "YourPrdSite",
        "protocol": "https",
        "port": "443",
    },
}
# =============================================================================


# ----------------------------------------------------------------------------
# Small XML attribute helpers. We edit the raw text instead of re-serializing
# the XML so everything else in the workbook stays byte-for-byte identical.
# ----------------------------------------------------------------------------
_ENT = {"'": "&apos;", '"': "&quot;"}
_UNENT = {"&apos;": "'", "&quot;": '"'}

TAG_RE = re.compile(r"<(connection|repository-location)\b[^>]*>")
DATASOURCE_TAG_RE = re.compile(r"<datasource\b[^>]*>")


def _attr_pattern(name):
    return re.compile(r"(\s" + re.escape(name) + r"\s*=\s*)(['\"])(.*?)\2", re.S)


def get_attr(tag, name):
    m = _attr_pattern(name).search(tag)
    return _xml_unescape(m.group(3), _UNENT) if m else None


def set_attr(tag, name, value):
    v = _xml_escape(value, _ENT)
    pat = _attr_pattern(name)
    if pat.search(tag):
        return pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{v}{m.group(2)}", tag, count=1)
    if tag.endswith("/>"):
        return tag[:-2].rstrip() + f" {name}='{v}' />"
    return tag[:-1].rstrip() + f" {name}='{v}'>"


def del_attr(tag, name):
    return re.sub(r"\s" + re.escape(name) + r"\s*=\s*(['\"]).*?\1", "", tag, count=1, flags=re.S)


# ----------------------------------------------------------------------------
# Prompt helpers
# ----------------------------------------------------------------------------
def say(msg=""):
    print(msg)


def ask(prompt, default=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    ans = input(f"{prompt}{suffix}: ").strip()
    return ans if ans else (default if default is not None else "")


def yes_no(prompt, default=None):
    hint = {True: "Y/n", False: "y/N", None: "y/n"}[default]
    while True:
        ans = input(f"{prompt} ({hint}): ").strip().lower()
        if not ans and default is not None:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        say("  Please type y or n.")


def pick_number(prompt, count):
    while True:
        ans = input(f"{prompt} (1-{count}): ").strip()
        if ans.isdigit() and 1 <= int(ans) <= count:
            return int(ans)
        say(f"  Please type a number from 1 to {count}.")


def clean_path(raw):
    # Handles drag-and-drop paths, which often come wrapped in quotes.
    p = raw.strip().strip('"').strip("'").strip()
    return os.path.abspath(os.path.expanduser(p)) if p else ""


# ----------------------------------------------------------------------------
# Reading / writing .twb and .twbx
# ----------------------------------------------------------------------------
def get_workbook_path(arg):
    raw = arg
    while True:
        if not raw:
            raw = input("Drag your .twb or .twbx file here (or type the path), then press Enter: ")
        path = clean_path(raw)
        raw = None
        if not path:
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".twb", ".twbx"):
            say(f"  '{os.path.basename(path)}' isn't a Tableau workbook. It needs to end in .twb or .twbx.")
            continue
        if not os.path.isfile(path):
            say(f"  Can't find that file: {path}")
            continue
        return path


def read_workbook(path):
    """Returns (xml_text, twb_member_name_or_None, had_bom)."""
    if path.lower().endswith(".twb"):
        with open(path, "rb") as f:
            raw = f.read()
        member = None
        say("  Detected: .twb (plain workbook)")
    else:
        if not zipfile.is_zipfile(path):
            raise SystemExit("  This .twbx file looks damaged (it isn't a valid package).")
        with zipfile.ZipFile(path) as z:
            twbs = [n for n in z.namelist() if n.lower().endswith(".twb")]
            if not twbs:
                raise SystemExit("  No .twb was found inside this .twbx.")
            root_level = [n for n in twbs if "/" not in n]
            member = (root_level or twbs)[0]
            raw = z.read(member)
        say(f"  Detected: .twbx (packaged workbook) - extracted '{member}'")

    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    return text, member, had_bom


def write_workbook(src_path, out_path, member, text, had_bom):
    data = text.encode("utf-8")
    if had_bom:
        data = b"\xef\xbb\xbf" + data
    if member is None:
        with open(out_path, "wb") as f:
            f.write(data)
        return
    # Rebuild the package: same contents, only the .twb swapped.
    with zipfile.ZipFile(src_path) as zin, zipfile.ZipFile(out_path, "w") as zout:
        for info in zin.infolist():
            payload = data if info.filename == member else zin.read(info.filename)
            zout.writestr(info, payload, compress_type=info.compress_type)


def unique_output_path(src_path, label):
    stem, ext = os.path.splitext(src_path)
    candidate = f"{stem}_{label}{ext}"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{stem}_{label}_{n}{ext}"
        n += 1
    return candidate


# ----------------------------------------------------------------------------
# Finding data sources
# ----------------------------------------------------------------------------
def find_published_datasources(text):
    """Returns an ordered list of dicts: content_url, caption, server, site."""
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        raise SystemExit(f"  Couldn't read the workbook XML ({e}). Is the file corrupted?")

    found = {}
    top = root.find("datasources")
    candidates = list(top) if top is not None else []
    for ds in candidates:
        if ds.tag != "datasource":
            continue
        conn = next((c for c in ds.iter("connection") if c.get("class") == "sqlproxy"), None)
        if conn is None:
            continue
        content_url = conn.get("dbname")
        if not content_url or content_url in found:
            continue
        repo = ds.find("repository-location")
        site = repo.get("site", "") if repo is not None else ""
        found[content_url] = {
            "content_url": content_url,
            "caption": ds.get("caption") or conn.get("server-ds-friendly-name") or content_url,
            "server": conn.get("server", "?"),
            "site": site,
        }
    return list(found.values())


# ----------------------------------------------------------------------------
# The actual rewrite
# ----------------------------------------------------------------------------
def transform(text, target, renames, caption_renames):
    """
    target  : dict(host, site, protocol, port) or None to keep current server
    renames : {old_content_url: new_content_url}
    caption_renames : {old_caption: new_caption}
    """

    def fix_tag(m):
        tag, kind = m.group(0), m.group(1)

        if kind == "connection":
            if get_attr(tag, "class") != "sqlproxy":
                return tag
            if target:
                tag = set_attr(tag, "server", target["host"])
                tag = set_attr(tag, "port", target["port"])
                tag = set_attr(tag, "channel", target["protocol"])
            db = get_attr(tag, "dbname")
            if db in renames:
                tag = set_attr(tag, "dbname", renames[db])
                if get_attr(tag, "server-ds-friendly-name") is not None:
                    tag = set_attr(tag, "server-ds-friendly-name", renames[db])
            return tag

        # <repository-location ...>
        path = get_attr(tag, "path") or ""
        segment = path.rstrip("/").split("/")[-1] if path else ""  # "datasources" or "workbooks"
        rid = get_attr(tag, "id")

        if segment == "datasources" and rid in renames:
            rid = renames[rid]
            tag = set_attr(tag, "id", rid)

        if target and segment:
            site = target["site"]
            path = (f"/t/{site}/" if site else "/") + segment
            tag = set_attr(tag, "path", path)
            tag = set_attr(tag, "site", site) if site else del_attr(tag, "site")

        derived = get_attr(tag, "derived-from")
        if derived and rid and path:
            parts = urlsplit(derived)
            scheme = target["protocol"] if target else (parts.scheme or "https")
            netloc = target["host"] if target else parts.netloc
            new = f"{scheme}://{netloc}{path}/{rid}"
            if parts.query:
                new += f"?{parts.query}"
            tag = set_attr(tag, "derived-from", new)
        return tag

    text = TAG_RE.sub(fix_tag, text)

    # Data pane label: only renamed if it still matched the old name.
    if caption_renames:
        def fix_caption(m):
            tag = m.group(0)
            cap = get_attr(tag, "caption")
            if cap in caption_renames:
                tag = set_attr(tag, "caption", caption_renames[cap])
            return tag
        text = DATASOURCE_TAG_RE.sub(fix_caption, text)

    # The workbook's own "home server" stamp.
    if target:
        text = re.sub(
            r"(<workbook\b[^>]*?\sxml:base\s*=\s*)(['\"])[^'\"]*\2",
            lambda m: f"{m.group(1)}{m.group(2)}{target['protocol']}://{target['host']}{m.group(2)}",
            text,
            count=1,
        )
    return text


# ----------------------------------------------------------------------------
# Interactive steps
# ----------------------------------------------------------------------------
def describe_server(cfg):
    site = cfg["site"] or "Default site"
    return f"{cfg['host']}  (site: {site})"


def parse_server_input(raw):
    """Accepts a bare hostname or a pasted browser URL like
    https://host/#/site/MySite/views/... and pulls out host + site."""
    raw = raw.strip()
    site = None
    m = re.search(r"/#?/?site/([^/?#]+)", raw)
    if m:
        site = m.group(1)
    proto = "https"
    if raw.lower().startswith("http://"):
        proto = "http"
    host = re.sub(r"^[a-z]+://", "", raw, flags=re.I).split("/")[0].split("#")[0]
    return host, site, proto


def choose_target():
    names = list(SERVERS)
    say("\nWhere should this workbook point?")
    for i, name in enumerate(names, 1):
        cfg = SERVERS[name]
        flag = "   <-- edit SERVERS at the top of the script!" if "yourcompany" in cfg["host"] else ""
        say(f"  {i}) {name:<6} {describe_server(cfg)}{flag}")
    say(f"  {len(names) + 1}) Custom server")
    say(f"  {len(names) + 2}) Keep the current server (only rename data sources)")

    choice = pick_number("Choose", len(names) + 2)

    if choice <= len(names):
        name = names[choice - 1]
        cfg = SERVERS[name]
        if "yourcompany" in cfg["host"]:
            say("\n  That entry still has the placeholder hostname.")
            say("  Open tableau_migrate.py and fill in SERVERS at the top first.")
            raise SystemExit(1)
        return name, dict(cfg)

    if choice == len(names) + 1:
        say("\nYou can paste the server's browser URL or just type the hostname.")
        while True:
            host, site_from_url, proto = parse_server_input(ask("Server"))
            if host and "." in host and " " not in host:
                break
            say("  That doesn't look like a server address (e.g. tableau.company.com).")
        if site_from_url is not None:
            site = ask("Site (detected from URL - press Enter to accept)", site_from_url)
        else:
            site = ask("Site name, as shown after /site/ in the URL (leave blank for Default site)", "")
        proto = ask("Protocol (https or http)", proto).lower()
        if proto not in ("https", "http"):
            proto = "https"
        port = ask("Port", "443" if proto == "https" else "80")
        label = re.sub(r"[^A-Za-z0-9_-]", "", host.split(".")[0]) or "custom"
        return label, {"host": host, "site": site.strip("/"), "protocol": proto, "port": port}

    return "renamed", None


def list_datasources(datasources, renames):
    say("\nPublished data sources in this workbook:\n")
    width = max(len(d["content_url"]) for d in datasources)
    for i, d in enumerate(datasources, 1):
        cur = d["content_url"]
        new = renames.get(cur)
        name = f"{cur}  ->  {new}" if new else cur
        say(f"  {i}. {name:<{width + 4}}")
    say("")


def choose_renames(datasources):
    renames, caption_renames = {}, {}
    if not yes_no("\nDo you want to rename any of the data sources?", default=False):
        return renames, caption_renames

    say("\n  Tip: use the name exactly as it appears at the END of the data source's")
    say("  URL on the new server, e.g.  .../datasources/Apple_1  ->  Apple_1")

    while True:
        list_datasources(datasources, renames)
        n = pick_number("Which one do you want to rename?", len(datasources))
        ds = datasources[n - 1]
        while True:
            new = ask(f"New name for '{ds['content_url']}'").strip()
            if not new:
                say("  Name can't be blank.")
                continue
            if re.search(r"[\s/\\?#'\"<>&]", new):
                say("  Heads up: server content URLs normally don't contain spaces or")
                say("  special characters. Double-check the end of the URL on the server.")
                if not yes_no("  Use it anyway?", default=False):
                    continue
            break
        if new == ds["content_url"]:
            renames.pop(ds["content_url"], None)
            say("  Same as the current name - no change.")
        else:
            renames[ds["content_url"]] = new
            if ds["caption"] in (ds["content_url"],):
                caption_renames[ds["caption"]] = new
            say(f"  OK: {ds['content_url']}  ->  {new}")

        if not yes_no("\nRename another one?", default=False):
            break
    return renames, caption_renames


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    say("=" * 60)
    say("  Tableau workbook migrator")
    say("  (your original file is never changed)")
    say("=" * 60)

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg in ("-h", "--help"):
        say(__doc__)
        return

    src = get_workbook_path(arg)
    text, member, had_bom = read_workbook(src)

    datasources = find_published_datasources(text)
    if not datasources:
        say("\n  This workbook has no PUBLISHED (server) data sources, so there's")
        say("  nothing to repoint. Embedded connections (direct to a database or")
        say("  Excel file) are not handled by this tool.")
        return

    servers_now = sorted({f"{d['server']}  (site: {d['site'] or 'Default site'})" for d in datasources})
    say("\nCurrently pointing at:")
    for s in servers_now:
        say(f"  - {s}")

    label, target = choose_target()
    datasources_listed = datasources
    list_datasources(datasources_listed, {})
    renames, caption_renames = choose_renames(datasources)

    if target is None and not renames:
        say("\nNothing to change. Exiting without writing a file.")
        return

    # Summary
    say("\n" + "-" * 60)
    say("Summary")
    say("-" * 60)
    if target:
        say(f"  Server : {describe_server(target)}")
    else:
        say("  Server : (unchanged)")
    if renames:
        say("  Renames:")
        for old, new in renames.items():
            say(f"    {old}  ->  {new}")
    else:
        say("  Renames: none")
    out = unique_output_path(src, label)
    say(f"  Output : {out}")
    say("-" * 60)

    if not yes_no("Write the new file?", default=True):
        say("Cancelled. Nothing was written.")
        return

    new_text = transform(text, target, renames, caption_renames)

    # Safety checks before writing anything.
    try:
        ET.fromstring(new_text.encode("utf-8"))
    except ET.ParseError as e:
        raise SystemExit(f"\n  Safety check failed - the edited XML is invalid ({e}). Nothing was written.")

    after = find_published_datasources(new_text)
    expected = {renames.get(d["content_url"], d["content_url"]) for d in datasources}
    actual = {d["content_url"] for d in after}
    if expected != actual:
        raise SystemExit("\n  Safety check failed - data sources didn't come out as expected. Nothing was written.")
    if target:
        wrong = [d for d in after if d["server"] != target["host"]]
        if wrong:
            raise SystemExit("\n  Safety check failed - some connections weren't repointed. Nothing was written.")

    write_workbook(src, out, member, new_text, had_bom)

    say(f"\nDone! Saved: {out}")
    say("\nNext steps:")
    if target:
        say(f"  1. In Tableau Desktop, sign in to {target['host']} first")
    else:
        say("  1. In Tableau Desktop, sign in to the server")
    say("  2. Open the new file and make sure the sheets load data")
    say("  3. Publish it")
    say("\nIf you get 'data source not found', compare each name above with the")
    say("end of that data source's URL on the server.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n\nCancelled. Nothing was written.")
        sys.exit(1)
