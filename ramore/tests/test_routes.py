import dataclasses
from pathlib import Path

import pytest

from ramore import config
from ramore.report import routes
from ramore.report.routes import UNKNOWN, Kind

from .conftest import API_ROUTES


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "/"),
        ("/about", "/about"),
        ("/about/", "/about"),
        ("https://www.example.org/about?x=1", "/about"),
        ("/authors", "/{rootType}/{...semanticId}"),
        ("/authors/table", "/{rootType}/table"),
        ("/authors/a/b", "/{rootType}/{...semanticId}"),
        ("/__data.json", "/__data.json"),
        ("/authors/table/__data.json", "/{rootType}/table/__data.json"),
        ("/authors/a/b/__data.json", "/{rootType}/{...semanticId}/__data.json"),
        ("/sitemap.xml", "/sitemap.xml"),
        ("/sitemap-3.xml", "/sitemap-{n}.xml"),
        ("/card/authors/a/share.png", "/card/{rootType}/{...semanticId}/{kind}.{ext}"),
        ("/quest", "/{rootType}/{...semanticId}"),
        ("/_app/immutable/x.js", "/_app/{...path}"),
        ("/v1/names/authors?q=x", "/v1/names/{etype}"),
        ("/v1/works-intersect/a/b", "/v1/works-intersect/{...spec}"),
        ("/v1/specs", "/v1/specs"),
        ("nope", UNKNOWN),
        ("", UNKNOWN),
    ],
)
def test_template(path: str, expected: str):
    assert routes.template(path) == expected


def test_kinds():
    t = routes.table()
    assert len(t.of_kind(Kind.API)) == len(API_ROUTES)
    assert "/callback" in t.of_kind(Kind.ENDPOINT)
    assert "/{rootType}/table" in t.of_kind(Kind.PAGE)
    assert "/{rootType}/table/__data.json" in t.of_kind(Kind.DATA)
    assert t.of_kind(Kind.ASSET) == {routes.APP_ASSETS}


def test_named_route_must_exist(cfg: config.Config):
    missing = dataclasses.replace(cfg.search, route="/v1/gone")
    config.use(dataclasses.replace(cfg, search=missing))
    with pytest.raises(ValueError, match="search"):
        routes.table()


def test_load(tmp_path: Path):
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "routes.py").write_text(
        f"from typing import Final\n\nAPI_ROUTES: Final = {API_ROUTES!r}\n"
    )
    (tmp_path / config.CONFIG_NAME).write_text(
        """name = "Example"
store = "store"

[source]
ssh = "example-live"
hosts = ["WWW.example.org"]

[routes]
api = "gen/routes.py:API_ROUTES"

[publish]
worktree = "/tmp/example-ghpages"
"""
    )
    cfg = config.load(tmp_path / config.CONFIG_NAME)
    assert cfg.store == tmp_path / "store"
    assert cfg.hosts == {"www.example.org"}
    assert cfg.api_routes == API_ROUTES
    assert cfg.log_path == config.DEFAULT_LOG
    assert cfg.sveltekit is None and cfg.search is None and cfg.login is None
