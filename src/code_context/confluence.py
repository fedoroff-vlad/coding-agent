"""Confluence REST sync — the automatic half of roadmap decision B.

The docs pipeline (C-3) reads an **exported corpus** off disk. That works, and it goes stale the
moment someone edits a page: re-exporting a space by hand is the step nobody repeats. This module
fetches the same pages over the REST API instead.

**It syncs to disk and stops there.** The fetched page is written as HTML into a corpus directory
and the existing ``ingest_docs`` runs over it unchanged — no second ingest path, and the archive
stays the greppable, diffable Layer-1 record the notes and `.docx`/`.pdf` passes already follow. A
failed sync therefore cannot corrupt the index; at worst the corpus is a page short.

Two shapes of Confluence, one client: **Data Center/Server** (API under ``/rest/api``, a Personal
Access Token as ``Bearer``) and **Cloud** (``/wiki/rest/api``, Basic auth with *email + API token*).
The email is what selects Basic — set it for Cloud, leave it empty for a PAT.

Content is **untrusted** the same way an exported page is: it arrives as a `doc` fragment carrying
`source`/`trust`, and nothing here interprets it. Page bodies are never logged — ids, titles and
counts only.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import httpx

from . import obs
from .config import settings

TOKEN_ENV = "CODE_CONTEXT_CONFLUENCE_TOKEN"
MANIFEST = ".confluence-sync.json"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def auth_headers(token: str, email: str = "") -> dict[str, str]:
    """``Bearer`` for a Data Center PAT, HTTP Basic for Cloud's *email + API token*.

    The email is the discriminator because it is the thing only Cloud has: a DC token is presented
    alone, and sending Basic with an empty username is a 401 that reads like a bad token.
    """
    if not token:
        return {}
    if email:
        pair = base64.b64encode(f"{email}:{token}".encode()).decode()
        return {"Authorization": f"Basic {pair}"}
    return {"Authorization": f"Bearer {token}"}


def api_root() -> str:
    """The REST root, e.g. ``https://wiki.example.internal/rest/api``."""
    base = settings.confluence_base_url.rstrip("/")
    if not base:
        raise RuntimeError(
            f"CODE_CONTEXT_CONFLUENCE_BASE_URL is not set — the wiki's site URL, "
            f"e.g. https://wiki.example.internal (Cloud: https://<site>.atlassian.net). "
            f"The token goes in {TOKEN_ENV}, never in a file."
        )
    return base + "/" + settings.confluence_api_path.strip("/")


def slug(page_id: str, title: str, max_len: int = 60) -> str:
    """A stable, path-safe filename stem: ``<id>-<title-slug>``.

    The **id leads** so a renamed page keeps its file — a title-only name would leave the old file
    behind on every rename and the corpus would grow duplicates of the same page.
    """
    s = _SLUG_STRIP.sub("-", title.lower()).strip("-")[:max_len].strip("-")
    return f"{page_id}-{s}" if s else str(page_id)


def page_html(title: str, body: str) -> str:
    """The page as the parser expects it: the title as an ``<h1>``, then the body.

    A REST body carries no title — an *exported* page does, as its top heading, and D-1 builds the
    section tree from headings. Without this the whole page would hang off whatever heading happens
    to come first, and `search_docs` would return sections nobody can place.
    """
    return f"<html><body><h1>{title}</h1>\n{body}</body></html>\n"


def _load_manifest(dest: Path) -> dict[str, int]:
    try:
        return json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt or absent manifest must not be fatal: the worst case is a full re-fetch, which
        # is correct, just slower. Silently trusting a half-written one would skip real updates.
        return {}


def _save_manifest(dest: Path, versions: dict[str, int]) -> None:
    (dest / MANIFEST).write_text(json.dumps(versions, indent=1, sort_keys=True), encoding="utf-8")


def fetch_pages(space: str, client: httpx.Client | None = None):
    """Yield ``(page_id, title, body_html, version)`` for every page in ``space``.

    Paginated with ``start``/``limit`` — the v1 content API both editions serve. ``body.view`` is
    the default expansion because it is *rendered*: a code macro comes back as ``<pre>`` and a table
    macro as a ``<table>``, which is exactly the shape D-1's parser was written against (an export
    is rendered HTML too). ``storage`` is available for a site that refuses view rendering, at the
    cost of macros arriving as ``<ac:structured-macro>`` the parser cannot see into.
    """
    fmt = settings.confluence_body_format
    expand = f"body.{fmt},version"
    owned = client is None
    client = client or httpx.Client(timeout=settings.confluence_timeout_s)
    headers = auth_headers(os.environ.get(TOKEN_ENV, ""), settings.confluence_email)
    start, limit = 0, settings.confluence_page_limit
    emitted: set[str] = set()
    try:
        while True:
            resp = client.get(
                f"{api_root()}/content",
                params={
                    "spaceKey": space,
                    "type": "page",
                    "status": "current",
                    "expand": expand,
                    "start": start,
                    "limit": limit,
                },
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results", [])
            if not results:
                return
            fresh = 0
            for page in results:
                page_id = str(page.get("id", ""))
                if page_id in emitted:
                    continue
                emitted.add(page_id)
                fresh += 1
                body = (page.get("body", {}).get(fmt, {}) or {}).get("value", "")
                yield (
                    page_id,
                    page.get("title", ""),
                    body,
                    int((page.get("version") or {}).get("number", 0)),
                )
            # Three stop conditions, because no single one is safe on its own:
            #  - the server says there is no next page AND this one was short → genuinely done;
            #  - trusting `_links.next` alone would truncate silently behind a proxy that strips it;
            #  - a whole batch of already-seen ids means the server ignored `start`, and looping on
            #    that is an infinite request loop against someone's wiki.
            has_next = bool((payload.get("_links") or {}).get("next"))
            if fresh == 0 or (not has_next and len(results) < limit):
                return
            start += len(results)
    finally:
        if owned:
            client.close()


def sync_space(space: str, dest_path: str, client: httpx.Client | None = None) -> dict[str, int]:
    """Fetch ``space`` into ``dest_path`` as HTML files. Returns counts.

    Incremental on Confluence's own ``version.number`` — the only change signal that is authoritative
    (a body hash would also re-fetch on a whitespace-only re-render, and a timestamp is not
    monotonic across a restore). Pages that vanished upstream are removed from the corpus, or
    `ingest_docs` would keep re-embedding a page nobody can reach any more.
    """
    dest = Path(dest_path)
    dest.mkdir(parents=True, exist_ok=True)
    known = _load_manifest(dest)
    seen: dict[str, int] = {}
    stats = {"pages": 0, "written": 0, "skipped": 0, "removed": 0}

    with obs.timed("confluence.sync", space=space) as ev:
        for page_id, title, body, version in fetch_pages(space, client):
            stats["pages"] += 1
            seen[page_id] = version
            target = dest / f"{slug(page_id, title)}.html"
            if known.get(page_id) == version and target.exists():
                stats["skipped"] += 1
                continue
            # A rename changes the filename; drop the stale twin so the corpus holds one file per
            # page. The id prefix makes the old file findable, which is why it leads the slug.
            for old in dest.glob(f"{page_id}-*.html"):
                if old != target:
                    old.unlink()
            target.write_text(page_html(title, body), encoding="utf-8")
            stats["written"] += 1

        for gone in set(known) - set(seen):
            for old in dest.glob(f"{gone}-*.html"):
                old.unlink()
                stats["removed"] += 1

        _save_manifest(dest, seen)
        ev.update(stats)
    return stats
