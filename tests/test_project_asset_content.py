"""项目 Asset 真实 HTTP 预览：Range、校验缓存和索引失效。"""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from conftest import HUMAN_TOKEN
from playground_fixtures import real_mp4_bytes


def test_project_video_ranges_and_cache_invalidation(http_server, journey, monkeypatch):
    journey.create_project()
    store = http_server.workspaces.structured_director_store(journey.project_id)
    content = real_mp4_bytes(24, 2.0)
    document = store.import_media_asset(
        media_type="video/mp4", content=content, expected_revision=1
    )
    asset = document.assets[0]
    url = f"{http_server.base_url}{journey.resource}/assets/{asset.id}/content"

    def request(byte_range=None):
        headers = {"Authorization": f"Bearer {HUMAN_TOKEN}"}
        if byte_range:
            headers["Range"] = byte_range
        return urlopen(Request(url, headers=headers), timeout=5)

    with request("bytes=0-1") as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == f"bytes 0-1/{len(content)}"
        assert response.read() == content[:2]
    with monkeypatch.context() as guard:
        guard.setattr(
            "core.structured_director.store._parse_document", lambda *_: pytest.fail("重复解析项目")
        )
        guard.setattr("hashlib.file_digest", lambda *_: pytest.fail("重复校验同一文件"))
        for byte_range, expected in [
            (None, content),
            ("bytes=-8", content[-8:]),
            ("bytes=9-", content[9:]),
        ]:
            with request(byte_range) as response:
                assert response.status == (206 if byte_range else 200)
                assert int(response.headers["Content-Length"]) == len(expected)
                assert response.read() == expected
        with pytest.raises(HTTPError) as error:
            request(f"bytes={len(content)}-")
        assert error.value.code == 416
        assert error.value.headers["Content-Range"] == f"bytes */{len(content)}"

    media_path = store.path.parent / asset.relative_path
    media_path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    with pytest.raises(HTTPError) as error:
        request("bytes=0-1")
    assert error.value.code == 409

    # 原子替换权威文档，保留媒体文件，确保旧索引无法继续供给该 Asset。
    state = json.loads(store.path.read_bytes())
    state["assets"] = []
    replacement = store.path.with_suffix(".tmp")
    replacement.write_text(json.dumps(state))
    replacement.replace(store.path)
    with pytest.raises(HTTPError) as error:
        request()
    assert error.value.code == 404
