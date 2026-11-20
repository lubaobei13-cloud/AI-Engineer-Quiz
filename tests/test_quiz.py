"""API regression tests against a fresh database per test."""
import csv
import io
import json
import threading

import pytest
from fastapi.testclient import TestClient
from keepstack import db
from keepstack.app import app
from keepstack.auth import hash_password, make_token
from keepstack.config import config
from tests.quiz_data import create_catalog


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    for field, value in dict(data_dir=tmp_path, db_path=tmp_path / "test.db",
                             blob_dir=tmp_path / "blobs", thumb_dir=tmp_path / "thumbs",
                             cache_dir=tmp_path / "cache").items():
        monkeypatch.setattr(config, field, value)
    monkeypatch.setattr(config, "ai_enabled", False)
    monkeypatch.setattr(config, "ocr_enabled", False)
    monkeypatch.setattr(db, "_local", threading.local())
    cols, records = create_catalog()
    conn = db.get_conn()
    conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                 ("reviewer", hash_password("test-password"), "admin", "2026-01-01"))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE username='reviewer'").fetchone()["id"]
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + make_token(uid, "admin")
        yield client, cols, records
    conn.close()


def filters(cols, **extra):
    return dict(q="harbor", collection_id=cols["quiz-main"],
                include_tags=["approved", "campaign"], exclude_tags=["internal"],
                media_type="image", **extra)


def exported(client, params):
    response = client.get("/api/assets/export", params=params)
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize("sort", ["oldest", "newest"])
def test_combined_filters_stable_pages_and_export(catalog, sort):
    client, cols, records = catalog
    params = filters(cols, sort=sort)
    expected = [r["uuid"] for r in sorted(records[:36], key=lambda r: (r["created_at"], r["id"]), reverse=sort == "newest")]
    found = []
    for offset in range(0, 36, 7):
        response = client.get("/api/assets", params={**params, "limit": 7, "offset": offset})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 36
        found.extend(r["uuid"] for r in response.json()["items"])
    assert found == expected
    assert len(set(found)) == 36
    assert client.get("/api/assets", params={**params, "offset": 999}).json()["items"] == []
    rows = exported(client, {**params, "limit": 1, "offset": 7})
    assert [r["uuid"] for r in rows] == found
    special = next(r for r in rows if r["uuid"] == records[0]["uuid"])
    assert special["description"] == '????, "??"\n???'
    assert json.loads(special["tags"]) == ["approved", "campaign"]


def test_tag_normalization_conflicts_and_empty_search(catalog):
    client, cols, records = catalog
    params = filters(cols)
    params["include_tags"] = [" APPROVED ", "campaign", "approved"]
    assert client.get("/api/assets", params=params).json()["total"] == 36
    params["exclude_tags"] = ["APPROVED", "approved"]
    assert client.get("/api/assets", params=params).json()["total"] == 0
    assert exported(client, params) == []
    assert client.get("/api/assets", params={"q": "   "}).json()["total"] == 43
    assert client.get("/api/assets", params={"q": '\"()* : - OR'}).status_code == 200
    assert client.get("/api/assets", params={"q": '\"()* : -'}).json()["total"] == 0
    # Legacy single-tag parameter and collection previously bound SQL arguments incorrectly.
    assert client.get("/api/assets", params={"tag": "approved", "collection_id": cols["quiz-main"]}).json()["total"] == 41
    params = filters(cols)
    params["exclude_tags"] = ["internal", "campaign"]
    assert exported(client, params) == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"limit": 201},
    {"offset": -1}, {"offset": "oops"}, {"collection_id": 0},
    {"sort": "injected SQL"}, {"status": "deleted"}, {"mode": "semantic"}])
def test_invalid_parameters_rejected(catalog, params):
    client, _, _ = catalog
    assert client.get("/api/assets", params=params).status_code == 422


def test_export_requires_login(catalog):
    client, _, _ = catalog
    client.headers.pop("Authorization")
    assert client.get("/api/assets/export").status_code == 401


def test_edits_reindex_queries_and_export(catalog):
    client, cols, records = catalog
    uuid = records[0]["uuid"]
    detail = "/api/assets/" + uuid
    response = client.patch(detail, json={"title": "Editedneedle ??", "description": "Changedcaption"})
    assert response.status_code == 200
    assert exported(client, {"q": "editedneedle"})[0]["title"] == "Editedneedle ??"
    assert client.get("/api/assets", params={"q": "changedcaption"}).json()["total"] == 1
    assert uuid not in [r["uuid"] for r in exported(client, filters(cols))]
    assert client.post(detail + "/tags", json={"name": "UniqueTag"}).status_code == 200
    assert client.get("/api/assets", params={"q": "uniquetag"}).json()["total"] == 1
    assert exported(client, {"include_tags": "uniquetag"})[0]["uuid"] == uuid
    assert client.delete(detail + "/tags/uniquetag").status_code == 200
    assert client.get("/api/assets", params={"q": "uniquetag"}).json()["total"] == 0
    assert exported(client, {"include_tags": "uniquetag"}) == []
