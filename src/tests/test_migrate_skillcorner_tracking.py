"""Tests for the RM in-place S3 migration adapter (in-memory fake S3; synthetic data only)."""

from __future__ import annotations

import gzip
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


class FakeS3:
    """Minimal in-memory S3 double: get/put/copy/delete + a truncating corrupt-PUT hook."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.corrupt_key: str | None = None

    def get_object(self, Bucket, Key):
        if Key not in self.store:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.store[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        data = Body if isinstance(Body, bytes) else Body.read()
        if Key == self.corrupt_key:
            data = data[: len(data) // 2]  # truncated PUT
        self.store[Key] = data

    def copy_object(self, Bucket, Key, CopySource):
        self.store[Key] = self.store[CopySource["Key"]]

    def delete_object(self, Bucket, Key):
        self.store.pop(Key, None)


def _snappy_table(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def _seed_rm_match(s3: FakeS3, mid: str) -> None:
    pref = f"skillcorner/_private/{mid}"
    frames = [
        {
            "frame": 0,
            "timestamp": "00:00:00.00",
            "period": 1,
            "ball_data": {"x": 1.0, "y": 2.0, "z": 0.0, "is_detected": True},
            "possession": {"player_id": 7, "group": "home"},
            "image_corners_projection": {
                k: 1.0
                for k in [
                    "x_top_left",
                    "y_top_left",
                    "x_bottom_left",
                    "y_bottom_left",
                    "x_bottom_right",
                    "y_bottom_right",
                    "x_top_right",
                    "y_top_right",
                ]
            },
            "player_data": [{"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True}],
        }
    ]
    s3.store[f"{pref}/tracking.json.gz"] = gzip.compress(json.dumps(frames).encode())
    for role in ("events", "physical", "freeze_frames"):
        s3.store[f"{pref}/{role}.parquet"] = _snappy_table(pa.table({"a": [1]}))
    s3.store[f"{pref}/metadata.json"] = b"{}"
    s3.store["skillcorner/matches.json"] = json.dumps(
        {
            "provider": "skillcorner",
            "matches": [
                {
                    "id": mid,
                    "visibility": "private",
                    "artifacts": {
                        "tracking": "tracking.json.gz",
                        "events": "events.parquet",
                        "physical": "physical.parquet",
                        "freeze_frames": "freeze_frames.parquet",
                        "metadata": "metadata.json",
                    },
                    "updated_at": "2026-06-29T00:00:00Z",
                }
            ],
        }
    ).encode()


@pytest.fixture
def mig(load_script):
    return load_script("migrate_skillcorner_tracking_parquet")


def test_migrate_produces_canonical_and_removes_legacy(mig):
    s3 = FakeS3()
    _seed_rm_match(s3, "1001")
    mig.migrate_match(s3, "b", "1001", dry_run=False)
    keys = set(s3.store)
    assert "skillcorner/_private/1001/tracking.parquet" in keys
    assert "skillcorner/_private/1001/tracking.json.gz" not in keys  # legacy gone
    assert "skillcorner/_private/1001/freeze_frames.parquet" not in keys  # freeze dropped
    idx = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
    assert idx["artifacts"]["tracking"] == "tracking.parquet"
    assert "freeze_frames" not in idx["artifacts"]
    assert idx["format_version"] == 2
    # events value-preservation end to end
    ev = pq.read_table(io.BytesIO(s3.store["skillcorner/_private/1001/events.parquet"]))
    assert ev.equals(pa.table({"a": [1]}))


def test_corrupt_stored_tracking_blocks_delete(mig):
    s3 = FakeS3()
    _seed_rm_match(s3, "1001")
    s3.corrupt_key = "skillcorner/_private/1001/tracking.parquet"  # truncate the PUT
    with pytest.raises(pa.ArrowInvalid):
        mig.migrate_match(s3, "b", "1001", dry_run=False)
    assert "skillcorner/_private/1001/tracking.json.gz" in s3.store  # NOT deleted


def test_rerun_is_noop(mig):
    s3 = FakeS3()
    _seed_rm_match(s3, "1001")
    mig.migrate_match(s3, "b", "1001", dry_run=False)
    before = dict(s3.store)
    mig.migrate_match(s3, "b", "1001", dry_run=False)  # idempotent
    assert set(s3.store) == set(before)


def test_dry_run_mutates_nothing(mig):
    s3 = FakeS3()
    _seed_rm_match(s3, "1001")
    before = dict(s3.store)
    mig.migrate_match(s3, "b", "1001", dry_run=True)
    assert s3.store == before


def test_guard_refuses_public_key(mig):
    with pytest.raises(ValueError, match="non-private"):
        mig._guard_key("skillcorner/1886347/tracking.parquet")  # a PUBLIC key


def test_rerun_sweeps_orphaned_legacy(mig):
    s3 = FakeS3()
    _seed_rm_match(s3, "1001")
    mig.migrate_match(s3, "b", "1001", dry_run=False)
    # simulate a crash AFTER the index flipped to v2 but BEFORE the legacy delete
    s3.store["skillcorner/_private/1001/tracking.json.gz"] = b"orphan"
    s3.store["skillcorner/_private/1001/freeze_frames.parquet"] = b"orphan"
    mig.migrate_match(s3, "b", "1001", dry_run=False)  # re-run must sweep them
    assert "skillcorner/_private/1001/tracking.json.gz" not in s3.store
    assert "skillcorner/_private/1001/freeze_frames.parquet" not in s3.store
