"""Deterministic quiz fixtures; also runnable with python -m tests.quiz_data."""
import io
from PIL import Image
from keepstack import ingest
from keepstack.db import get_conn, init_db


def create_catalog():
    init_db()
    conn = get_conn()
    if conn.execute("SELECT 1 FROM collections WHERE uuid='quiz-main'").fetchone():
        raise ValueError("Quiz data already exists; use a fresh KEEPSTACK_DATA_DIR")
    for name in ("main", "other"):
        conn.execute("INSERT INTO collections (uuid, name, created_at) VALUES (?,?,?)",
                     ("quiz-" + name, "Quiz " + name, "2026-01-01T00:00:00Z"))
    conn.commit()
    cols = {r["uuid"]: r["id"] for r in conn.execute("SELECT id, uuid FROM collections")}
    records = []
    for i in range(44):
        image = io.BytesIO()
        Image.new("RGB", (64, 64), (i * 5, 40, 120)).save(image, format="PNG")
        a = ingest.ingest_stream(io.BytesIO(image.getvalue()), f"quiz_{i:02}.png", run_ai=False)
        tags = ["approved", "campaign"]
        title = f"Harbor {i:02}"
        status, media, collection = "active", "image", cols["quiz-main"]
        if i == 36:
            tags.append("internal")
        elif i == 37:
            tags.remove("campaign")
        elif i == 38:
            collection = cols["quiz-other"]
        elif i == 39:
            status = "deleted"
        elif i == 40:
            title = "Mountain"
        elif i == 41:
            media = "document"
        elif i == 42:
            tags = ["approved-extra", "campaign"]
        elif i == 43:
            tags.append("internal")
        description = '????, "??"\n???' if i == 0 else f"Description {i}"
        created = f"2026-01-{1 + i // 12:02}T00:00:00Z"
        conn.execute("UPDATE assets SET title=?, description=?, status=?, media_type=?, created_at=? WHERE id=?",
                     (title, description, status, media, created, a["id"]))
        conn.execute("INSERT INTO collection_assets (collection_id, asset_id) VALUES (?,?)", (collection, a["id"]))
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            conn.execute("INSERT INTO asset_tags (asset_id, tag_id) SELECT ?, id FROM tags WHERE name=?", (a["id"], tag))
        conn.commit()
        ingest.reindex_fts(a["id"])
        records.append(dict(uuid=a["uuid"], id=a["id"], title=title, created_at=created))
    return cols, records


if __name__ == "__main__":
    from keepstack.app import _startup
    _startup()
    cols, records = create_catalog()
    print(f"Created {len(records)} quiz assets; collection: {cols['quiz-main']}")
    print("Keyword: harbor; include: approved + campaign; exclude: internal; media: image")
    print("Expected: 36 matches; admin/admin on a fresh default configuration")
