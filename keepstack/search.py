"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - faceted structured filters (media type, tag, status, dates)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

Facet counts are returned alongside results so the UI can render a live,
drill-down sidebar.
"""
from __future__ import annotations

from typing import Optional

from . import ai
from .db import get_conn

SORTS = {
    "newest": "a.created_at DESC, a.id DESC",
    "oldest": "a.created_at ASC, a.id ASC",
    "name": "a.title ASC, a.id ASC",
    "largest": "a.size DESC, a.id DESC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query."""
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def _matching_query(*, q="", media_type=None, tag=None, include_tags=None,
                    exclude_tags=None, collection_id=None, status="active",
                    sort="newest", mode="keyword"):
    """One predicate/order definition for counts, pages and CSV exports."""
    where, params = ["a.status = ?"], [status]
    if q.strip():
        fts = _fts_query(q)
        # Punctuation-only input contains no searchable tokens: match nothing.
        if fts:
            where.append("a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)")
            params.append(fts)
        else:
            where.append("0")
    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)
    if collection_id is not None:
        where.append("EXISTS (SELECT 1 FROM collection_assets ca "
                     "WHERE ca.asset_id = a.id AND ca.collection_id = ?)")
        params.append(collection_id)
    included = {t.strip().lower() for t in [*(include_tags or []), *([tag] if tag else [])] if t.strip()}
    excluded = {t.strip().lower() for t in (exclude_tags or []) if t.strip()}
    for names, negate in ((included, False), (excluded, True)):
        for name in sorted(names):
            where.append(("NOT " if negate else "") +
                         "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                         "WHERE at.asset_id = a.id AND t.name = ?)")
            params.append(name)
    return " AND ".join(where), params, SORTS.get(sort, SORTS["newest"])


def search(*, q="", media_type=None, tag=None, include_tags=None, exclude_tags=None,
           collection_id=None, status="active", sort="newest", mode="keyword",
           limit=60, offset=0) -> dict:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("limit must be 1..200 and offset must be non-negative")
    if mode == "semantic" and q.strip():
        return _semantic_search(q, media_type, status, limit, offset)
    conn = get_conn()
    where, params, order = _matching_query(
        q=q, media_type=media_type, tag=tag, include_tags=include_tags,
        exclude_tags=exclude_tags, collection_id=collection_id, status=status, sort=sort)
    total = conn.execute(f"SELECT COUNT(*) AS c FROM assets a WHERE {where}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset]).fetchall()
    return {"total": total, "items": [_card(dict(r)) for r in rows],
            "facets": _facets(status), "mode": mode}


def export_rows(**filters):
    """Read all matches, batching tag lookups rather than issuing one per asset."""
    conn = get_conn()
    where, params, order = _matching_query(**filters)
    cursor = conn.execute(
        "SELECT a.id, a.uuid, a.title, a.description, a.filename, a.media_type, a.created_at "
        f"FROM assets a WHERE {where} ORDER BY {order}", params)
    while batch := cursor.fetchmany(200):
        ids = [r["id"] for r in batch]
        tags = {aid: [] for aid in ids}
        for row in conn.execute(
            "SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id IN ({','.join('?' for _ in ids)}) ORDER BY t.name", ids):
            tags[row["asset_id"]].append(row["name"])
        for row in batch:
            item = dict(row)
            item["tags"] = tags[item.pop("id")]
            yield item


def _semantic_search(q, media_type, status, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    where = ["status = ?", "embedding IS NOT NULL"]
    params: list = [status]
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)
    rows = conn.execute(
        f"SELECT * FROM assets WHERE {' AND '.join(where)}", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets(status), "mode": "semantic"}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
    }


def _card(a: dict) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "created_at": a["created_at"],
    }
