#!/usr/bin/env python3
"""eco-harvest.py — harvest the ENTIRE bash plugin ecosystem into a manifest.

Owner directive (2026-10-05, verbatim intent): "ALL bash plugin ecosystem
assets must be audited — 几十万 at least, every single one, across the ENTIRE
ecosystem. Not samples." This is the harvest-and-test infrastructure lane
(wt92/harvest). The universe (captain-verified via GitHub API): ~119k
bash-related repos, topic:dotfiles 25k, ~35k files with PROMPT_COMMAND
assignments, bash-it ~100 themes, oh-my-bash 84 themes.

DUAL PURPOSE (captain scope-upgrade, wt92): the manifest is also the index
database for the full-screen TUI (#186). Per-asset rows carry TUI-facing
fields (name, category, verdict badge) and join to a `repos` table holding
stars / description / license / upstream URL. T1 curated-framework assets
are the TUI's built-in catalog from day one; T2-T5 are the searchable long
tail (progressive: show what is indexed, harvest continues in background).
The tester (eco-test.py) stamps the per-asset verdict badge (OK / SLOW /
HANG / ERROR-STORM / SYNTAX-REJECT-RUBASH-ONLY / GNU-ALSO-FAILS) — the
trust/quality signal lazy.nvim does not have.

Design (the two constraints that shape everything):

1.  Raw content is fetched ON DEMAND by the tester, never bulk-fetched here.
    Dedup therefore uses the **git blob SHA** (every entry of
    `GET /repos/{o}/{r}/git/trees/{branch}?recursive=1` carries the blob's
    SHA-1): the same prompt fragment in 10k dotfiles is ONE asset, known at
    harvest time with zero content fetches. `hash_kind` records the guarantee;
    code-search hits (T4) carry no blob sha, so the tester computes sha256 on
    its on-demand fetch and backfills the manifest.

2.  Rate budgets are logged, never guessed: every api.github.com response's
    X-RateLimit-* headers append to state/rate-budget.jsonl, and secondary
    limits sleep until reset (bounded) instead of burning the run.

Tiers (mission §1 + captain category upgrade):
  T1 curated frameworks  — oh-my-bash, bash-it, oh-my-zsh bash-runnable
                           corners, basherpm/basher, bats-core (testing).
                           THE TUI BUILT-IN CATALOG.
  T2 completion/keybind/init — bash-completion (1000+ per-command FILES),
                           fzf key-bindings, ble.sh, zoxide/atuin/direnv/mise
                           bash init hooks
  T3 topic/repos         — topic:bash-plugin, topic:bash-script,
                           topic:bats, topic:shellcheck, topic:bash-library,
                           prompt/framework/keybinding searches, stars desc
  T4 code-search sweep   — PROMPT_COMMAND / PS1= assignments: each hit is a
                           prompt-fragment asset
  T5 dotfiles crawl      — topic:dotfiles bashrc extracts (the long tail)

Categories (TUI tabs): theme, plugin, completion, alias, prompt-fragment,
keybinding, init-hooks, test-framework, linter, tool, framework, unknown.

Output: manifest SQLite (+ assets.jsonl sidecar) under
D:/eco-harvest/manifest/, census.json (BY TIER AND BY CLASS), resumable
state under D:/eco-harvest/state/. Restartable at every level:
harvested_repos table skips done repos, search cursors persist, asset
inserts are idempotent.

Usage:
  python scripts/harvest/eco-harvest.py --tier t1
  python scripts/harvest/eco-harvest.py --tier t3 --t3-repos 200
  python scripts/harvest/eco-harvest.py --tier all --workers 16   # full run (owner-gated)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

NOW = lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Tier definitions — (repo, origin, category, [include regexes over paths])
# ---------------------------------------------------------------------------

# T1: curated frameworks = the TUI's built-in catalog.
T1_SOURCES = [
    ("ohmybash/oh-my-bash", "oh-my-bash", None, [
        # Current upstream suffixes are .theme.sh/.plugin.sh/.completion.sh;
        # older releases and forks use .themes.bash/.plugin.bash — cover both.
        r"^themes/[^/]+/[^/]+\.(themes?\.bash|theme\.sh)$",
        r"^plugins/[^/]+/[^/]+\.(plugin\.bash|plugin\.sh)$",
        r"^completions/[^/]+\.(completion\.bash|completion\.sh)$",
        r"^aliases/[^/]+\.(aliases\.bash|aliases\.sh)$",
        r"^lib/[^/]+\.(bash|sh)$",
    ]),
    ("bash-it/bash-it", "bash-it", None, [
        r"^themes/[^/]+/[^/]+\.theme\.bash$",
        r"^plugins/available/[^/]+\.bash$",
        r"^aliases/available/[^/]+\.aliases\.bash$",
        r"^completion/available/[^/]+\.bash$",
        r"^lib/[^/]+\.bash$",
    ]),
    # oh-my-zsh's bash-runnable corners only: tools/*.sh carry bash shebangs;
    # the .zsh plugin/theme bodies are NOT bash-runnable and are deliberately
    # excluded (harvest honesty beats asset count).
    ("ohmyzsh/ohmyzsh", "ohmyzsh-bash-corners", "tool", [
        r"^tools/[^/]+\.sh$",
        r"^[^/]+\.sh$",
    ]),
    ("basherpm/basher", "basher", "framework", [
        r"^(lib|libexec)/[^/]+\.(bash|sh)$",
        r"^completion/[^/]+\.bash$",
    ]),
    # The bash testing framework (captain category gap: testing frameworks).
    ("bats-core/bats-core", "bats-core", "test-framework", [
        r"^lib/[^/]+\.bash$",
        r"^libexec/bats-core/[^/]+$",    # engine files carry no extension
        r"^(test|install)\.sh$",
    ]),
]

# T2: completion / keybinding / init-hook repos.
T2_SOURCES = [
    # NOTE: canonical repo is scop/bash-completion; the `bash-completion`
    # org name 404s (verified 2026-10-05 via the API). Since 2.12 the
    # per-command completion FILES live under completions-core/ +
    # completions-fallback/ (completions/ holds only release packaging).
    ("scop/bash-completion", "bash-completion", "completion", [
        r"^(completions-core|completions-fallback)/[^/]+\.bash$",
        r"^(bash_completion|[^/]+)\.bash$",
    ]),
    ("junegunn/fzf", "fzf", "keybinding", [
        r"^shell/[^/]+\.bash$",
    ]),
    ("akinomyoga/ble.sh", "ble-sh", "keybinding", [
        r"^ble\.sh$",
        r"^lib/[^/]+\.sh$",
        r"^src/[^/]+\.sh$",
    ]),
    ("ajeetdsouza/zoxide", "zoxide", "init-hooks", [
        r"^contrib/.*\.bash$",
    ]),
    ("direnv/direnv", "direnv", "init-hooks", [
        r"^(stdlib|dotenv)\.sh$",
    ]),
    ("jdx/mise", "mise", "init-hooks", [
        r"^[^/]+\.sh$",
    ]),
    ("atuinsh/atuin", "atuin", "init-hooks", [
        r"^[^/]+\.sh$",
    ]),
]

# T3: repo-search queries (stars desc, per-query caps share the total).
T3_QUERIES = [
    "topic:bash-plugin",
    "topic:bash-script",
    "topic:bats",
    "topic:shellcheck",
    "topic:bash-library",
    "bash prompt theme in:name,description",
    "shell framework in:name,description",
    "bash key bindings in:name,description",
    "topic:bash",
]
T3_ENTRYPOINT_SUFFIX = re.compile(r"\.(sh|bash)$", re.IGNORECASE)
T3_ENTRYPOINT_EXCLUDE = re.compile(
    r"(^|/)(test|tests|spec|specs|docs?|examples?|vendor|node_modules"
    r"|\.github)(/|$)", re.IGNORECASE)

# T4: code-search sweep. Legacy search/code caps at 1000 results/query;
# each hit = one prompt-fragment asset.
T4_QUERIES = [
    "PROMPT_COMMAND language:Shell",
    "PS1= filename:.bashrc",
    "PS1= filename:.bash_profile",
]

# T5: dotfiles crawl — bashrc-family basenames inside topic:dotfiles repos.
# Optional leading dot, bash-family core name, ANY trailing suffix (popular
# layouts use `bashrc.symlink`, `bashrc.example`, …).
T5_BASENAMES = re.compile(
    r"^\.?(bashrc|bash_profile|bash_aliases|bash_functions|bash_exports|"
    r"bash_paths|profile|aliases)(\.[^/\\]*)?$",
    re.IGNORECASE)

# ---------------------------------------------------------------------------
# Category classifier (TUI tabs)
# ---------------------------------------------------------------------------

CATEGORY_RULES = [
    ("theme", re.compile(r"theme|prompt/", re.IGNORECASE)),
    ("completion", re.compile(r"completion|completions/", re.IGNORECASE)),
    ("alias", re.compile(r"aliases|alias", re.IGNORECASE)),
    ("keybinding", re.compile(r"key.?bind|inputrc|bind_", re.IGNORECASE)),
    ("prompt-fragment", re.compile(r"prompt|ps1", re.IGNORECASE)),
    ("test-framework", re.compile(r"\.bats$|bats|test", re.IGNORECASE)),
    ("linter", re.compile(r"lint|shellcheck", re.IGNORECASE)),
    ("init-hooks", re.compile(r"(hook|init|integrat)", re.IGNORECASE)),
    ("plugin", re.compile(r"plugin", re.IGNORECASE)),
    ("framework", re.compile(r"(lib|framework|core|base)", re.IGNORECASE)),
]


def path_category(path: str, origin: str | None = None,
                  forced: str | None = None) -> str:
    """Category from the per-source override, else path heuristics. The
    tester refines from real content (first line + body signals) on its
    on-demand fetch."""
    if forced:
        return forced
    if origin == "oh-my-bash":
        if ".themes." in path:
            return "theme"
        if ".plugin." in path:
            return "plugin"
        if ".completion." in path:
            return "completion"
        if ".aliases." in path:
            return "alias"
        return "framework"
    if origin == "bash-it":
        if "/themes/" in path:
            return "theme"
        if "plugins/" in path:
            return "plugin"
        if "completion/" in path:
            return "completion"
        if "aliases/" in path:
            return "alias"
        return "framework"
    if origin == "bats-core":
        return "test-framework"
    low = path.lower()
    if low.endswith(".bats"):
        return "test-framework"
    for cat, rx in CATEGORY_RULES:
        if rx.search(low):
            return cat
    return "unknown"


# ---------------------------------------------------------------------------
# GitHub API client with rate-budget logging
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class Github:
    # Sustained secondary/abuse limiting: after this many consecutive
    # backoff waits without a successful call, give up and let the tier
    # supervisor retry (a pathological token/policy problem, not a window).
    MAX_SECONDARY_WAITS = 40

    def __init__(self, token: str, budget_log: Path):
        self.token = token
        self.budget_log = budget_log
        self.budget_lock = threading.Lock()
        self.calls = 0
        self.waited_seconds = 0.0
        self._secondary_waits = 0

    def _log_budget(self, kind: str, resp):
        try:
            rem = resp.headers.get("x-ratelimit-remaining")
            lim = resp.headers.get("x-ratelimit-limit")
            reset = resp.headers.get("x-ratelimit-reset")
        except Exception:
            rem = lim = reset = None
        with self.budget_lock:
            with open(self.budget_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": NOW(), "kind": kind,
                    "remaining": rem, "limit": lim, "reset": reset}) + "\n")

    def _wait_for_reset(self, resp, kind: str):
        """Rate-limit class failure: wait out the window. The old design
        slept at most 700s x 4 attempts and then RAISED — any reset window
        further away than ~47min (core windows run to a full hour) or any
        sustained secondary/abuse limit killed the whole unattended run.
        Now: primary exhaustion (x-ratelimit-remaining == 0) sleeps until
        the reset header, looping as often as needed; secondary limits
        (Retry-After or no reset info) take a bounded exponential backoff
        and give up only after MAX_SECONDARY_WAITS consecutive waits.
        Neither consumes the caller's retry attempts (wt97b)."""
        try:
            reset = int(resp.headers.get("x-ratelimit-reset", "0"))
        except ValueError:
            reset = 0
        try:
            retry_after = float(resp.headers.get("retry-after", "0") or 0)
        except ValueError:
            retry_after = 0.0
        now = int(time.time())
        target = max(reset, now + int(retry_after))
        if target > now:
            sleep_s = min(target - now + 2, 3500)
        else:
            self._secondary_waits += 1
            if self._secondary_waits > self.MAX_SECONDARY_WAITS:
                raise RuntimeError(
                    f"{kind}: secondary rate limit persisted through "
                    f"{self._secondary_waits} consecutive backoff waits")
            sleep_s = min(30 * 2 ** min(self._secondary_waits - 1, 5), 900)
        log(f"RATE {kind}: budget exhausted; sleeping {sleep_s}s "
            f"(lane rule: log budget, never burn the run)")
        time.sleep(sleep_s)
        self.waited_seconds += sleep_s

    def get(self, path: str, kind: str = "core", retries: int = 4):
        """GET an API path, returning parsed JSON. Rate-limit responses
        (403/429 with an exhausted window) sleep until reset WITHOUT
        consuming retries; other 403/429 (abuse/secondary) back off within
        the retry budget; transient 5xx and network faults retry."""
        url = path if path.startswith("http") else API + path
        attempt = 0
        while True:
            self.calls += 1
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "niubash-eco-harvest/wt92",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self._log_budget(kind, resp)
                    self._secondary_waits = 0
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                self._log_budget(kind, err)
                if err.code in (403, 429):
                    body = err.read().decode("utf-8", "replace").lower()
                    if err.headers.get("x-ratelimit-remaining") == "0" or \
                            "rate limit" in body:
                        self._wait_for_reset(err, kind)
                        continue
                    attempt += 1
                    if attempt <= retries:
                        time.sleep(2 * attempt)  # abuse / secondary backoff
                        continue
                    raise
                if err.code >= 500:
                    attempt += 1
                    if attempt <= retries:
                        time.sleep(2 * attempt)
                        continue
                    raise
                if err.code in (404, 409):
                    # 409: empty repository (no commits) — nothing to harvest.
                    return None
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                attempt += 1
                if attempt <= retries:
                    time.sleep(2 * attempt)
                    continue
                raise

    def search(self, query: str, kind: str, page: int, per_page: int = 100,
               sort: str | None = None):
        q = urllib.parse.urlencode(
            {"q": query, "per_page": per_page, "page": page,
             **({"sort": sort, "order": "desc"} if sort else {})})
        return self.get(f"/search/{kind}?{q}", kind=kind)


# ---------------------------------------------------------------------------
# Manifest store — dual-purpose: audit manifest + TUI index (#186)
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS repos (
    full_name     TEXT PRIMARY KEY,
    description   TEXT,
    stars         INTEGER,
    license       TEXT,
    upstream_url  TEXT,
    meta_done     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS assets (
    content_hash    TEXT PRIMARY KEY,
    hash_kind       TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    repo            TEXT NOT NULL,
    path            TEXT NOT NULL,
    commit_sha      TEXT,
    bytes           INTEGER,
    first_line_class TEXT,
    category        TEXT,
    tier            TEXT NOT NULL,
    origin          TEXT,
    name            TEXT,
    discovered_at   TEXT,
    fetched         INTEGER DEFAULT 0,
    verdict         TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);
CREATE INDEX IF NOT EXISTS idx_assets_tier ON assets(tier);
CREATE INDEX IF NOT EXISTS idx_assets_verdict ON assets(verdict);
CREATE TABLE IF NOT EXISTS occurrences (
    content_hash TEXT, repo TEXT, path TEXT, commit_sha TEXT, tier TEXT,
    UNIQUE(content_hash, repo, path, commit_sha)
);
CREATE TABLE IF NOT EXISTS harvested_repos (
    tier TEXT, repo TEXT, tree_sha TEXT, head_commit TEXT,
    files INTEGER, ts TEXT,
    PRIMARY KEY (tier, repo)
);
-- TUI index view (#186): asset + repo metadata + audit verdict badge.
CREATE VIEW IF NOT EXISTS asset_index AS
SELECT a.content_hash, a.name, COALESCE(a.category, 'unknown') AS category,
       r.description, r.stars, r.license, r.upstream_url,
       a.source_url, a.repo, a.path, a.tier, a.origin,
       a.first_line_class, a.bytes,
       CASE WHEN a.verdict IN ('OK', 'SLOW') THEN a.verdict
            WHEN a.verdict IS NULL THEN 'unaudited'
            ELSE a.verdict END AS badge
FROM assets a LEFT JOIN repos r ON r.full_name = a.repo;
"""


class Manifest:
    def __init__(self, manifest_dir: Path):
        manifest_dir.mkdir(parents=True, exist_ok=True)
        self.dir = manifest_dir
        self.db = sqlite3.connect(manifest_dir / "eco-manifest.sqlite",
                                  check_same_thread=False,
                                  timeout=30)  # coexist with eco-test verdict writes
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.jsonl = manifest_dir / "assets.jsonl"

    # -- writers (all under one lock; one WAL connection) -------------------
    def add_asset(self, content_hash, hash_kind, repo, path, commit, size,
                  first_line_class, category, tier, origin) -> bool:
        """Idempotent insert keyed by content-hash. Returns True if NEW.
        The same content in another repo/path = one asset + one occurrence."""
        name = Path(path).name
        source_url = raw_url(repo, commit, path)
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO assets VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL)",
                (content_hash, hash_kind, source_url, repo, path, commit,
                 size, first_line_class, category, tier, origin, name, NOW()))
            self.db.execute(
                "INSERT OR IGNORE INTO occurrences VALUES (?,?,?,?,?)",
                (content_hash, repo, path, commit, tier))
            if cur.rowcount:
                with open(self.jsonl, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "content_hash": content_hash, "hash_kind": hash_kind,
                        "source_url": source_url, "repo": repo, "path": path,
                        "commit": commit, "bytes": size,
                        "first_line_class": first_line_class,
                        "category": category, "tier": tier, "origin": origin,
                        "name": name}) + "\n")
                return True
        return False

    def note_repo_meta(self, meta: dict):
        """Store TUI-facing repo metadata (stars/description/license/URL)."""
        if not isinstance(meta, dict) or "full_name" not in meta:
            return
        lic = (meta.get("license") or {}).get("spdx_id")
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO repos(full_name,description,stars,license,"
                "upstream_url,meta_done) VALUES (?,?,?,?,?,1) "
                "ON CONFLICT(full_name) DO UPDATE SET description=excluded."
                "description, stars=excluded.stars, license=excluded.license,"
                " upstream_url=excluded.upstream_url, meta_done=1",
                (meta["full_name"], meta.get("description"),
                 meta.get("stargazers_count"), lic
                 if lic and lic != "NOASSERTION" else None,
                 meta.get("html_url")))

    def note_repo(self, tier, repo, tree_sha, head_commit, files):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO harvested_repos VALUES (?,?,?,?,?,?)",
                (tier, repo, tree_sha, head_commit, files, NOW()))

    def repo_done(self, tier, repo) -> bool:
        with self.lock:
            row = self.db.execute(
                "SELECT 1 FROM harvested_repos WHERE tier=? AND repo=?",
                (tier, repo)).fetchone()
        return row is not None

    def set_verdict(self, content_hash: str, verdict: str):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE assets SET fetched=1, verdict=? WHERE content_hash=?",
                (verdict, content_hash))

    def backfill_repo_meta(self, gh: "Github", limit: int = 0) -> int:
        """One core call per repo for repos lacking TUI metadata. Bounded by
        the core budget; call repeatedly across sessions (resumable)."""
        rows = self.db.execute(
            "SELECT full_name FROM repos WHERE meta_done=0").fetchall()
        if limit:
            rows = rows[:limit]
        n = 0
        for (repo,) in rows:
            meta = gh.get(f"/repos/{repo}")
            if meta:
                self.note_repo_meta(meta)
                n += 1
        return n

    def census(self) -> dict:
        assets = dict(self.db.execute(
            "SELECT tier, COUNT(*) FROM assets GROUP BY tier"))
        occ = dict(self.db.execute(
            "SELECT tier, COUNT(*) FROM occurrences GROUP BY tier"))
        byclass = dict(self.db.execute(
            "SELECT COALESCE(category,'unknown'), COUNT(*) "
            "FROM assets GROUP BY 1"))
        return {
            "assets_total": sum(assets.values()),
            "assets_per_tier": assets,
            "occurrences_total": sum(occ.values()),
            "occurrences_per_tier": occ,
            "dedup_ratio": (round(sum(occ.values()) / sum(assets.values()), 3)
                            if sum(assets.values()) else 0.0),
            "assets_per_category": byclass,
            "repos_harvested": dict(self.db.execute(
                "SELECT tier, COUNT(*) FROM harvested_repos GROUP BY tier")),
            "repos_with_tui_meta": self.db.execute(
                "SELECT COUNT(*) FROM repos WHERE meta_done=1").fetchone()[0],
        }


def raw_url(repo: str, commit: str | None, path: str) -> str:
    ref = commit or "HEAD"
    # #408: path segments go on the wire percent-encoded, one segment at a
    # time (keeps '/' separators). Literal spaces and '&' in filenames made
    # raw.githubusercontent reject the request — 74 space-bearing and 4
    # ampersand-bearing rows all landed FETCH-FAILED while the encoded form
    # of the same URL fetches 200. Raw URLs carry no query today; if one
    # ever appears it must not be encoded here (eco-test's fetch-time
    # encoder passes everything from '?' on through untouched).
    quoted = "/".join(urllib.parse.quote(seg, safe="")
                      for seg in path.split("/"))
    return f"{RAW}/{repo}/{ref}/{quoted}"


# ---------------------------------------------------------------------------
# Repo-tree harvesting (one tree+head call per repo; content never fetched)
# ---------------------------------------------------------------------------

def harvest_repo_tree(gh: Github, man: Manifest, tier: str, repo: str,
                      origin: str, category: str | None,
                      include: re.Pattern, cap: int,
                      basename_filter: re.Pattern | None = None) -> int:
    """Enumerate a repo's tree, add every matching blob as an asset
    (deduped by git blob sha). Returns occurrence count; -1 = already done."""
    if man.repo_done(tier, repo):
        return -1
    meta = gh.get(f"/repos/{repo}")
    if not isinstance(meta, dict) or "default_branch" not in meta:
        log(f"{tier} SKIP {repo}: meta unavailable (gone/private)")
        man.note_repo(tier, repo, None, None, 0)
        return 0
    man.note_repo_meta(meta)
    branch = meta["default_branch"]
    tree = gh.get(f"/repos/{repo}/git/trees/{branch}?recursive=1")
    if not isinstance(tree, dict) or "tree" not in tree:
        log(f"{tier} SKIP {repo}: tree unavailable (empty or too large)")
        man.note_repo(tier, repo, None, None, 0)
        return 0
    head = gh.get(f"/repos/{repo}/commits?per_page=1")
    commit = head[0]["sha"] if isinstance(head, list) and head else branch

    blobs = [e for e in tree["tree"]
             if e.get("type") == "blob" and include.search(e["path"])]
    if basename_filter is not None:
        blobs = [e for e in blobs
                 if basename_filter.match(Path(e["path"]).name)]
    # Control characters in a path would poison the raw URL later
    # (urllib: "URL can't contain control characters").
    blobs = [e for e in blobs
             if all(ch.isprintable() for ch in e["path"])]
    # Entry-point preference: root-level first, then shortest path (the
    # entry point of a plugin repo is its shallowest .sh/.bash, not docs).
    blobs.sort(key=lambda e: (e["path"].count("/"), len(e["path"]),
                              e["path"]))
    blobs = blobs[:cap]
    n = 0
    for e in blobs:
        man.add_asset(e["sha"], "git-blob-sha1", repo, e["path"], commit,
                      e.get("size"), None,
                      path_category(e["path"], origin, category),
                      tier, origin)
        n += 1
    man.note_repo(tier, repo, tree.get("sha"), commit, n)
    return n


def t1_t2_run(gh: Github, man: Manifest, tier: str, sources, workers: int,
              cap: int) -> int:
    jobs = [(repo, orig, cat,
             re.compile("|".join(f"(?:{p})" for p in pats)))
            for repo, orig, cat, pats in sources]
    total = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(harvest_repo_tree, gh, man, tier, repo, orig,
                            cat, inc, cap): repo
                for repo, orig, cat, inc in jobs}
        for fut in concurrent.futures.as_completed(futs):
            repo = futs[fut]
            try:
                n = fut.result()
            except Exception as err:
                log(f"{tier} ERROR {repo}: {err}")
                continue
            if n < 0:
                log(f"{tier} {repo}: already harvested (resume)")
            else:
                total += max(n, 0)
                log(f"{tier} {repo}: {n} assets")
    return total


# ---------------------------------------------------------------------------
# T3 / T5: repo-search driven
# ---------------------------------------------------------------------------

def search_repos(gh: Github, tier: str, query: str, max_repos: int,
                 state: dict, state_path: Path):
    """Yield repos from a search query, stars desc, resuming from the
    persisted page cursor."""
    key = f"{tier}:{query}"
    cur = state.setdefault("search_cursor", {}).setdefault(key, {"page": 1})
    got = state.setdefault("search_got", {}).setdefault(key, 0)
    while got < max_repos:
        res = gh.search(query, "repositories", cur["page"], sort="stars")
        items = res.get("items", []) if isinstance(res, dict) else []
        if not items:
            cur["done"] = True
            break
        for it in items:
            if got >= max_repos:
                break
            got += 1
            yield it["full_name"]
        cur["page"] += 1
        save_state(state, state_path)


def t3_t5_run(gh: Github, man: Manifest, tier: str, max_repos: int,
              workers: int, cap: int, state: dict, state_path: Path,
              include: re.Pattern, basename_filter: re.Pattern | None,
              queries: list[str], label: str, category: str | None = None):
    repos: list[str] = []
    seen = set()
    per_query = (max(max_repos // len(queries), 1) if max_repos
                 else 50_000)
    for q in queries:
        # One query failing after retries must not kill the whole run
        # (wt97b: unattended survival) — keep the repos gathered so far.
        try:
            for repo in search_repos(gh, tier, q, per_query, state,
                                     state_path):
                if repo.lower() not in seen:
                    seen.add(repo.lower())
                    repos.append(repo)
        except Exception as err:
            log(f"{tier} search '{q}' failed: {err} — continuing with "
                f"{len(repos)} repos gathered so far")
    log(f"{tier} {label}: {len(repos)} repos to enumerate "
        f"(cap {cap} entry-points each)")
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(harvest_repo_tree, gh, man, tier, repo, None,
                            category, include, cap,
                            basename_filter): repo for repo in repos}
        for fut in concurrent.futures.as_completed(futs):
            repo = futs[fut]
            try:
                fut.result()
            except Exception as err:
                log(f"{tier} ERROR {repo}: {err}")
            done += 1
            if done % 25 == 0:
                log(f"{tier}: {done}/{len(repos)} repos enumerated; "
                    f"assets={man.census()['assets_total']}")
                # Heartbeat (wt97b): enumeration can run ~an hour between
                # search-phase saves; touch the state file so its age stays
                # a meaningful liveness signal for monitors.
                save_state(state, state_path)
    save_state(state, state_path)


# ---------------------------------------------------------------------------
# T4: code-search sweep (hits carry no blob sha -> tester backfills sha256)
# ---------------------------------------------------------------------------

def t4_run(gh: Github, man: Manifest, state: dict, state_path: Path,
           max_per_query: int = 1000):
    for q in T4_QUERIES:
        key = f"t4:{q}"
        cur = state.setdefault("search_cursor", {}).setdefault(
            key, {"page": 1})
        added = state.setdefault("search_got", {}).setdefault(key, 0)
        while added < max_per_query:
            try:
                res = gh.search(q, "code", cur["page"], per_page=100)
            except Exception as err:
                log(f"t4 search '{q}' failed: {err}")
                break
            items = res.get("items", []) if isinstance(res, dict) else []
            if not items:
                break
            for it in items:
                repo = it.get("repository", {}).get("full_name")
                path = it.get("path")
                if not repo or not path:
                    continue
                # Code-search hits are NOT filtered by the tree harvester's
                # printable-path rule; control characters in a path produce
                # a raw URL urllib can never fetch ("URL can't contain
                # control characters") — a permanent FETCH-FAILED. Skip at
                # harvest time (wt97b; 18 such rows had already accumulated).
                if not all(ch.isprintable() for ch in path):
                    continue
                sha = it.get("sha") or f"t4:{repo}:{path}"
                man.add_asset(sha, "code-search-sha", repo, path, None,
                              it.get("size"), None,
                              path_category(path, None, "prompt-fragment"),
                              "t4", "code-search")
                added += 1
            cur["page"] += 1
            save_state(state, state_path)
            log(f"t4 '{q}': {added} hits so far")
        log(f"t4 '{q}': total {added} prompt-fragment assets")


# ---------------------------------------------------------------------------
# State + main
# ---------------------------------------------------------------------------

def save_state(state: dict, path: Path):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Harvest the bash plugin ecosystem into the eco manifest "
                    "(audit manifest + TUI #186 index)")
    ap.add_argument("--tier", default="t1", help="t1,t2,t3,t4,t5 or all")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--t3-repos", type=int, default=200,
                    help="T3 repo cap (0 = full universe, days)")
    ap.add_argument("--t5-repos", type=int, default=2000,
                    help="T5 repo cap (0 = full 25k universe)")
    ap.add_argument("--max-files-per-repo", type=int, default=25)
    ap.add_argument("--t1-max-files-per-repo", type=int, default=4000,
                    help="T1 curated frameworks are the TUI built-in "
                         "catalog: harvest them FULLY, no 25-file cap")
    ap.add_argument("--t2-max-files-per-repo", type=int, default=4000,
                    help="bash-completion is FILES-not-repos; needs a big cap")
    ap.add_argument("--manifest-dir", default="D:/eco-harvest/manifest")
    ap.add_argument("--state-dir", default="D:/eco-harvest/state")
    ap.add_argument("--backfill-repo-meta", type=int, default=0,
                    help="after harvest, fill TUI repo metadata for N repos "
                         "(resumable; one core call each)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    manifest_dir = Path(args.manifest_dir)
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "harvest-state.json"
    token = subprocess.run(["gh", "auth", "token"], capture_output=True,
                           text=True).stdout.strip()
    if not token:
        print("FATAL: `gh auth token` empty — harvest needs gh CLI auth")
        return 2
    gh = Github(token, state_dir / "rate-budget.jsonl")
    man = Manifest(manifest_dir)
    state = load_state(state_path)

    tiers = ([t.strip() for t in args.tier.split(",")]
             if args.tier != "all" else ["t1", "t2", "t3", "t4", "t5"])

    def run_tier(tier: str):
        if tier == "t1":
            t1_t2_run(gh, man, "t1", T1_SOURCES, args.workers,
                      args.t1_max_files_per_repo)
        elif tier == "t2":
            t1_t2_run(gh, man, "t2", T2_SOURCES, args.workers,
                      args.t2_max_files_per_repo)
        elif tier == "t3":
            t3_t5_run(gh, man, "t3", args.t3_repos, args.workers,
                      args.max_files_per_repo, state, state_path,
                      T3_ENTRYPOINT_SUFFIX, None, T3_QUERIES, "topic-repos")
        elif tier == "t4":
            t4_run(gh, man, state, state_path)
        elif tier == "t5":
            # Permissive include: .bashrc-family paths do NOT end in .sh/.bash
            # (`.bashrc` ends in `bashrc`), so the BASENAME filter decides.
            t3_t5_run(gh, man, "t5", args.t5_repos, args.workers,
                      args.max_files_per_repo, state, state_path,
                      re.compile(r"."), T5_BASENAMES,
                      ["topic:dotfiles"], "dotfiles")
        else:
            print(f"unknown tier {tier}")
            return 2
        return 0

    # Tier-level supervisor (wt97b unattended-survival fix): a tier that
    # still crashes after the retry ladder is logged and SKIPPED so the
    # remaining tiers (and the census write) still happen; everything is
    # resumable, so the next scheduled run continues where this stopped.
    t0 = time.time()
    for tier in tiers:
        for attempt in range(1, 4):
            try:
                if run_tier(tier) == 2:
                    return 2
                break
            except Exception:
                log(f"tier {tier} attempt {attempt}/3 crashed:\n"
                    f"{traceback.format_exc()}")
                if attempt < 3:
                    time.sleep(60 * attempt)
        else:
            log(f"tier {tier}: giving up after 3 crashed attempts")

    if args.backfill_repo_meta:
        n = man.backfill_repo_meta(gh, args.backfill_repo_meta)
        log(f"repo-meta backfill: {n} repos")

    census = man.census()
    (manifest_dir / "census.json").write_text(
        json.dumps(census, indent=2), encoding="utf-8")
    log("CENSUS " + json.dumps(census))
    log(f"api calls={gh.calls} secondary-wait={gh.waited_seconds:.0f}s "
        f"wall={time.time()-t0:.0f}s budget-log={gh.budget_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
