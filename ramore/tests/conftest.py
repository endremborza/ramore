from pathlib import Path

import pytest

from ramore import config

API_ROUTES = (
    "/v1/names/:etype",
    "/v1/trees/:root_type/:semantic_id",
    "/v1/works-intersect/*spec",
    "/v1/specs",
)
SVELTEKIT_DIRS = {
    "(stat)": "+page.svelte",
    "(stat)/about": "+page.svelte",
    "(stat)/[rootType]/table": "+page.svelte",
    "(stat)/[rootType]/[...semanticId]": "+page.svelte",
    "(game)/[game=quest]": "+page.svelte",
    "callback": "+server.ts",
    "robots.txt": "+server.ts",
    "sitemap.xml": "+server.ts",
    "sitemap-[n].xml": "+server.ts",
    "card/[rootType]/[...semanticId]/[kind].[ext=cardext]": "+server.ts",
}
SEARCH = config.Search(route="/v1/names/{etype}", param="q")
LOGIN = config.Login(route="/callback", ok_status=302, label="Logins")


def make_config(root: Path) -> config.Config:
    for rel, name in SVELTEKIT_DIRS.items():
        d = root / "src" / "routes" / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / name).touch()
    return config.Config(
        name="Example",
        root=root,
        store=root / "store",
        ssh_host="example-live",
        log_path=config.DEFAULT_LOG,
        hosts=frozenset({"www.example.org", "example.org", "api.example.org"}),
        api_routes=API_ROUTES,
        sveltekit=root,
        search=SEARCH,
        login=LOGIN,
        site_include={},
        ghpages_worktree=root / "ghpages",
        ghpages_branch=config.DEFAULT_BRANCH,
        ghpages_remote=config.DEFAULT_REMOTE,
    )


@pytest.fixture(autouse=True)
def cfg(tmp_path: Path) -> config.Config:
    cfg = config.use(make_config(tmp_path))
    cfg.ensure_dirs()
    return cfg
