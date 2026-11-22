#!/usr/bin/env python3

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import time
import textwrap
import tomllib
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).parent
API = "https://api.github.com"

# ---------------------------------------------------------------- テーマ
THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "text": "#c9d1d9",
        "key": "#ffa657", "value": "#a5d6ff", "dots": "#6e7681",
        "add": "#3fb950", "del": "#f85149", "art": "#c9d1d9",
        "doc": "#d2a8ff",
    },
    "light": {
        "bg": "#f6f8fa", "border": "#d0d7de", "text": "#24292f",
        "key": "#953800", "value": "#0a3069", "dots": "#8c959f",
        "add": "#1a7f37", "del": "#cf222e", "art": "#24292f",
        "doc": "#8250df",
    },
}

INFO_WIDTH = 62          
FONT_SIZE = 16
CHAR_W = FONT_SIZE * 0.6 
LINE_H = 20
PAD = 20
GAP_CHARS = 3            


# ---------------------------------------------------------------- GitHub API
class GitHub:
    def __init__(self, token: str):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "profile-readme-generator",
        }

    def _request(self, url: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub API error: HTTP {e.code} ({url.split('?')[0]})") from None

    def graphql(self, query: str, variables: dict) -> dict:
        _, res = self._request(f"{API}/graphql", {"query": query, "variables": variables})
        if res.get("errors"):
            msgs = "; ".join(err.get("message", "") for err in res["errors"])
            raise RuntimeError(f"GraphQL error: {msgs}")
        return res["data"]

    def rest(self, path: str) -> tuple[int, object]:
        return self._request(f"{API}{path}")


REPO_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) { totalCount }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { nameWithOwner stargazerCount isFork }
    }
  }
}"""

COMMIT_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) { totalCommitContributions }
  }
}"""


def fetch_stats(gh: GitHub, login: str) -> dict:
    repos, cursor, user = [], None, None
    while True:
        user = gh.graphql(REPO_QUERY, {"login": login, "cursor": cursor})["user"]
        page = user["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    commits, start = 0, created
    while start < now:
        end = min(start + dt.timedelta(days=365), now)
        data = gh.graphql(COMMIT_QUERY, {"login": login, "from": start.isoformat(), "to": end.isoformat()})
        commits += data["user"]["contributionsCollection"]["totalCommitContributions"]
        start = end

    add = dele = 0
    for repo in (r for r in repos if not r["isFork"]):
        weeks = contributor_weeks(gh, repo["nameWithOwner"], login)
        add += sum(w["a"] for w in weeks)
        dele += sum(w["d"] for w in weeks)

    return {
        "repos": user["repositories"]["totalCount"],
        "contributed": user["repositoriesContributedTo"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "followers": user["followers"]["totalCount"],
        "commits": commits,
        "add": add, "del": dele, "loc": add - dele,
    }


def contributor_weeks(gh: GitHub, name: str, login: str) -> list[dict]:
    for _ in range(6):
        status, body = gh.rest(f"/repos/{name}/stats/contributors")
        if status == 202:
            time.sleep(5)
            continue
        if status == 204 or not body:
            return []
        for c in body:
            if c.get("author") and c["author"]["login"].lower() == login.lower():
                return c["weeks"]
        return []
    print(f"warn: stats not ready for {name}, skipped", file=sys.stderr)
    return []


SAMPLE_STATS = {"repos": 42, "contributed": 57, "stars": 128, "followers": 64,
                "commits": 1234, "add": 98765, "del": 12345, "loc": 86420}


# ---------------------------------------------------------------- Text Formatting
def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def uptime(birthday: str) -> str:
    if not birthday:
        return "-"
    b, t = dt.date.fromisoformat(birthday), dt.date.today()
    months = (t.year - b.year) * 12 + (t.month - b.month)
    if _add_months(b, months) > t:
        months -= 1
    days = (t - _add_months(b, months)).days
    y, m = divmod(months, 12)
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
    return f"{plural(y, 'year')}, {plural(m, 'month')}, {plural(days, 'day')}"


Seg = tuple[str, str]  
PLACEHOLDER = re.compile(r"\{(\w+)\}(\+\+|--)?") 


def render_value(template: str, values: dict) -> list[Seg]:
    """Break template down into segments"""
    segs, pos = [], 0
    for m in PLACEHOLDER.finditer(template):
        if m.start() > pos:
            segs.append((template[pos:m.start()], "value"))
        key = m.group(1)
        v = values.get(key, m.group(0))
        text = f"{v:,}" if isinstance(v, int) else str(v)
        segs.append((text + (m.group(2) or ""), key if key in ("add", "del") else "value"))
        pos = m.end()
    if pos < len(template):
        segs.append((template[pos:], "value"))
    return segs


def seg_len(segs: list[Seg]) -> int:
    return sum(len(t) for t, _ in segs)


def kv(key: str, value: list[Seg], width: int) -> list[Seg]:
    """'. Key: ....... value' aligns with width"""
    head = [(". ", "dots"), (key, "key"), (":", "text")]
    dots = max(width - seg_len(head) - seg_len(value) - 2, 1)
    return head + [(" " + "." * dots + " ", "dots")] + value


def kv_wrapped(key: str, value: list[Seg], width: int) -> list[list[Seg]]:
    """Text folding"""
    head_len = len(". ") + len(key) + len(":")
    room = width - head_len - 4 
    if seg_len(value) <= room or len(value) != 1:
        return [kv(key, value, width)]
    parts = value[0][0].split(", ")
    chunks, cur = [], ""
    for i, part in enumerate(parts):
        piece = part + ("," if i < len(parts) - 1 else "")
        limit = room if not chunks else width - 4
        candidate = f"{cur} {piece}" if cur else piece
        if cur and len(candidate) > limit:
            chunks.append(cur)
            cur = piece
        else:
            cur = candidate
    chunks.append(cur)
    lines = [kv(key, [(chunks[0], "value")], width)]
    for c in chunks[1:]:
        lines.append([(". ", "dots"), (" " * (width - 2 - len(c)), "dots"), (c, "value")])
    return lines


def doc_lines(text: str) -> list[list[Seg]]:
    """Docstring ish self intro"""
    lines: list[list[Seg]] = [[('"""', "dots")]]
    for raw in text.strip("\n").split("\n"):
        raw = raw.rstrip()
        if not raw.strip():
            lines.append([])
            continue
        indent = len(raw) - len(raw.lstrip())
        bullet = re.match(r"\s*([-*・]|\d+\.)\s+", raw)
        hang = " " * (bullet.end() if bullet else indent)
        for row in textwrap.wrap(raw.strip(), INFO_WIDTH,
                                 initial_indent=" " * indent, subsequent_indent=hang):
            lines.append([(row, "doc")])
    lines.append([('"""', "dots")])
    return lines


def build_lines(cfg: dict, values: dict) -> list[list[Seg]]:
    lines: list[list[Seg]] = []
    title = cfg["title"]
    lines.append([(title, "key"), (" " + "-" * (INFO_WIDTH - len(title) - 4) + "-—-", "dots")])
    for section in cfg["section"]:
        if section.get("name"):
            lines.append([])
            head = f"- {section['name']} "
            lines.append([(head, "text"), ("-" * (INFO_WIDTH - len(head) - 3) + "-—-", "dots")])
        if section.get("bio"):
            lines += doc_lines(section["bio"])
        for item in section.get("items", []):
            if not item[0]:
                lines.append([(".", "dots")])
            elif len(item) == 4: 
                left_w = 36
                left = kv(item[0], render_value(item[1], values), left_w)
                right = kv(item[2], render_value(item[3], values), INFO_WIDTH - left_w - 1)
                right[0] = (" ", "dots")
                lines.append(left + [(" |", "text")] + right)
            else:
                lines += kv_wrapped(item[0], render_value(item[1], values), INFO_WIDTH)

    for segs in lines:
        text = "".join(t for t, _ in segs)
        if any(unicodedata.east_asian_width(ch) in "WF" for ch in text):
            print(f"warn: 全角文字は幅がずれます → {text.strip()[:30]}", file=sys.stderr)
        elif len(text) > INFO_WIDTH:
            print(f"warn: Exceeded {INFO_WIDTH} words → {text.strip()[:30]}", file=sys.stderr)
    return lines


# ---------------------------------------------------------------- SVG
def fit(chars: int) -> str:
    """Fixate length per line"""
    return f' textLength="{chars * CHAR_W:.1f}" lengthAdjust="spacing"' if chars > 1 else ""


def svg(art: list[str], info: list[list[Seg]], theme: dict) -> str:
    art_w = max((len(l) for l in art), default=0)
    info_x = PAD + (art_w + GAP_CHARS) * CHAR_W
    rows = max(len(art), len(info))
    width = round(info_x + INFO_WIDTH * CHAR_W + PAD)
    height = PAD * 2 + rows * LINE_H
    art_top = (rows - len(art)) // 2
    info_top = (rows - len(info)) // 2

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-size="{FONT_SIZE}" '
        f'font-family="Consolas, Menlo, \'DejaVu Sans Mono\', \'Courier New\', monospace">',
        "<style>",
        "text{white-space:pre}",
        *(f".{k}{{fill:{v}}}" for k, v in theme.items() if k not in ("bg", "border")),
        "</style>",
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" '
        f'fill="{theme["bg"]}" stroke="{theme["border"]}"/>',
    ]
    base_y = PAD + LINE_H - 5
    for i, line in enumerate(art):
        y = base_y + (art_top + i) * LINE_H
        out.append(f'<text x="{PAD}" y="{y}" class="art" xml:space="preserve"'
                   f'{fit(len(line))}>{escape(line)}</text>')
    for i, segs in enumerate(info):
        if not segs:
            continue
        y = base_y + (info_top + i) * LINE_H
        spans = "".join(f'<tspan class="{c}">{escape(t)}</tspan>' for t, c in segs)
        out.append(f'<text x="{info_x:.1f}" y="{y}" xml:space="preserve"{fit(seg_len(segs))}>{spans}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "profile.toml").read_text(encoding="utf-8"))
    load = lambda f: (ROOT / f).read_text(encoding="utf-8").rstrip("\n").split("\n")
    art = load("ascii.txt")
    art_light = load("ascii_light.txt") if (ROOT / "ascii_light.txt").exists() else art

    if args.sample:
        stats = SAMPLE_STATS
    else:
        token = os.environ.get("GH_TOKEN")
        if not token:
            print("error: Please set up GH_TOKEN first (You can preview with --sample)", file=sys.stderr)
            return 1
        stats = fetch_stats(GitHub(token), cfg["username"])

    values = {**stats, "uptime": uptime(cfg.get("birthday", ""))}
    info = build_lines(cfg, values)
    for name, theme in THEMES.items():
        a = art_light if name == "light" else art
        (ROOT / f"{name}_mode.svg").write_text(svg(a, info, theme), encoding="utf-8")
    print("generated: dark_mode.svg, light_mode.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
