#!/usr/bin/env python3

from playwright.sync_api import sync_playwright, TimeoutError
from urllib.parse import (
    urljoin,
    urlparse,
    urldefrag,
    parse_qsl,
    urlencode,
    urlunparse,
)
from collections import deque, defaultdict
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

import csv
import json
import re
import time


# ============================================================
# CONFIG
# ============================================================

START_URL = "https://www.mlb.com/player/maximo-acosta-69118511"

# When no more links remain, automatically start a new, independent crawl
# from the next URL that hasn't been visited yet. You can replace these
# examples with MLB player URLs or other MLB pages of your choice.
# Each new seed is a NEW ROOT (not falsely marked as discovered from a link).
FALLBACK_START_URLS = [
    "https://www.mlb.com/player/wilyer-abreu-67780011",
    "https://www.mlb.com/player/freddy-peralta-64254711",
    "https://www.mlb.com/player/chris-roycroft-6882973",
    "https://www.mlb.com/player/connor-thomas-671162",
    "https://www.mlb.com/player/michael-toglia-669911",
    "https://www.mlb.com/player/rob-zastryzny-642239",
    "https://www.mlb.com/player/jameson-taillon-592791",
    "https://www.mlb.com/orioles/stats",
    "https://www.mlb.com/pirates/stats",
    "https://www.mlb.com/reds/stats",
    "https://www.mlb.com/stats/1920",
    "https://www.mlb.com/stats/1950",
    "https://www.mlb.com/stats/1970",
    "https://www.mlb.com/stats/2000"
]

OUTPUT_DIR = Path("stored_players")

MAX_PLAYERS = 30000
MAX_PAGES = 100000000

# Be polite to MLB.
REQUEST_DELAY = 0.4

# Extra waiting after dynamic UI actions.
NORMAL_WAIT_MS = 1000
GAME_WAIT_MS = 2500
GAME_LOG_WAIT_MS = 1800

# Discovery: real MLB-wide stats links, historic year selectors, page controls,
# prospect/minor-league sections. No player URLs are constructed by ID.
ENABLE_GLOBAL_STATS = True
ENABLE_PROSPECTS = True
ENABLE_INTERACTIVE_PAGINATION = True
ENABLE_SEASON_SELECTION = True
MAX_TABLE_PAGES_PER_URL = 120
MAX_SEASON_CHOICES = 155  # historical seasons, only if actually offered by MLB UI
MIN_SEASON = 1876

# A checkpoint is written after each completed page, so a stop/restart
# normally loses at most the page that was in progress.
RESUME = True
STATE_FILE = OUTPUT_DIR / "crawl_state.json"
STATE_VERSION = 3

# Shows how many unique URLs were filtered out, vs already processed.
PRINT_DISCOVERY_DIAGNOSTICS = True


# ============================================================
# REGEX
# ============================================================

PLAYER_PATH_RE = re.compile(
    r"^/player/(?:[^/?#]+-)?(\d+)/?$"
)

GAME_ID_RE = re.compile(
    r"/gameday/(?:[^/]+/)*(\d{6,})"
)


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(base_url, href):
    if not href:
        return None

    href = href.strip()

    if href.startswith(
        (
            "javascript:",
            "mailto:",
            "tel:",
            "data:",
        )
    ):
        return None

    url = urljoin(base_url, href)

    # Remove #fragment
    url, _ = urldefrag(url)

    return url


def is_mlb_url(url):
    try:
        hostname = urlparse(url).hostname

        return hostname in {
            "mlb.com",
            "www.mlb.com",
        }

    except Exception:
        return False


def path_parts(url):
    return [
        part
        for part in urlparse(url).path.split("/")
        if part
    ]


def clean_query(url):
    """
    Keep meaningful query parameters, but remove tracking junk.
    """

    parsed = urlparse(url)

    items = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True
    ):
        key_lower = key.lower()

        if key_lower.startswith("utm_"):
            continue

        if key_lower in {
            "msockid",
            "partnerid",
            "affiliateid",
        }:
            continue

        items.append(
            (key, value)
        )

    items.sort()

    return urlencode(
        items,
        doseq=True
    )


def normalized_generic_url(url):
    parsed = urlparse(url)

    path = parsed.path.rstrip("/")

    if not path:
        path = "/"

    query = clean_query(url)

    return urlunparse(
        (
            "https",
            "www.mlb.com",
            path,
            "",
            query,
            "",
        )
    )


# ============================================================
# PLAYER
# ============================================================

def player_id(url):
    path = urlparse(url).path.rstrip("/")

    match = PLAYER_PATH_RE.match(path)

    if match:
        return match.group(1)

    return None


def is_player_url(url):
    return player_id(url) is not None


# ============================================================
# PAGE TYPES
# ============================================================

def is_scores_url(url):
    parts = path_parts(url)

    # /scores
    # /scores/2026-10-13
    if parts and parts[0] == "scores":
        return True

    # /redsox/scores
    # /redsox/scores/2026-10-13
    if (
        len(parts) >= 2
        and parts[1] == "scores"
    ):
        return True

    return False


def is_team_stats_url(url):
    parts = path_parts(url)

    if len(parts) < 2:
        return False

    # Avoid enormous unrelated sections such as
    # /prospects/stats
    if parts[0] in {
        "prospects",
        "stats",
    }:
        return False

    return parts[1] == "stats"


def is_roster_url(url):
    parts = path_parts(url)

    if len(parts) < 2:
        return False

    return parts[1] == "roster"


def is_game_url(url):
    path = urlparse(url).path.lower()

    return "/gameday/" in path



def is_global_stats_url(url):
    """League-wide statistics, including /stats, /stats/pitching, /stats/1901."""
    parts = path_parts(url)
    return bool(parts and parts[0] == "stats")


def is_prospect_url(url):
    """Prospects/minors pages hosted on mlb.com (never a fabricated URL)."""
    parts = path_parts(url)
    if not parts:
        return False
    if parts[0] == "prospects":
        return len(parts) == 1 or parts[1] in {
            "stats", "top100", "top-100", "top30", "top-30",
            "rankings", "team", "teams",
        }
    if parts[0] in {"milb", "minor-league", "minor-leagues"}:
        return any(part in {"stats", "roster", "players", "prospects"}
                   for part in parts[1:])
    return False


def is_historical_stats_view(url):
    return is_global_stats_url(url) or is_team_stats_url(url)

def page_type(url):
    if is_player_url(url):
        return "PLAYER"
    if is_team_stats_url(url):
        return "STATS"
    if is_global_stats_url(url):
        return "GLOBAL_STATS"
    if is_prospect_url(url):
        return "PROSPECTS"
    if is_roster_url(url):
        return "ROSTER"
    if is_scores_url(url):
        return "SCORES"
    if is_game_url(url):
        return "GAME"
    return "OTHER"



# ============================================================
# TEAM HELPERS
# ============================================================

def team_slug(url):
    """
    For:

        /redsox/stats
        /redsox/scores
        /redsox/roster

    return:

        redsox
    """

    parts = path_parts(url)

    if len(parts) < 2:
        return None

    if parts[1] in {
        "stats",
        "scores",
        "roster",
    }:
        return parts[0]

    return None


def same_team(url1, url2):
    team1 = team_slug(url1)
    team2 = team_slug(url2)

    return (
        team1 is not None
        and team2 is not None
        and team1 == team2
    )


# ============================================================
# CANONICAL KEY
# ============================================================

def url_key(url):
    """
    Critical deduplication function.

    PLAYER:
        /player/702332
        /player/caleb-durbin-702332

    both become:

        player:702332


    GAME:
        /gameday/849813/final/box
        /gameday/x-vs-y/.../849813/final/box

    both become:

        game:849813


    IMPORTANT:
    Score dates and stats queries are NOT thrown away anymore.
    """

    pid = player_id(url)

    if pid:
        return f"player:{pid}"

    if is_game_url(url):
        path = urlparse(url).path

        match = GAME_ID_RE.search(path)

        if match:
            return f"game:{match.group(1)}"

        return normalized_generic_url(url)

    if is_scores_url(url):
        # Preserve:
        #
        # /redsox/scores/2026-10-13
        # /redsox/scores/2026-10-12
        #
        # as DIFFERENT pages.
        return normalized_generic_url(url)

    if is_team_stats_url(url):
        # Preserve query parameters such as:
        #
        # ?playerPool=ROOKIES
        # ?season=2025
        # etc.
        return normalized_generic_url(url)

    if is_roster_url(url):
        return normalized_generic_url(url)

    return normalized_generic_url(url)


# ============================================================
# DYNAMIC PAGE LOADING
# ============================================================

def settle_page(page, kind):
    """
    Give dynamic MLB components a chance to render.

    We scroll because MLB tables / box scores may lazily
    render lower parts of the page.
    """

    wait = (
        GAME_WAIT_MS
        if kind == "GAME"
        else NORMAL_WAIT_MS
    )

    page.wait_for_timeout(wait)

    if kind not in {
        "STATS",
        "ROSTER",
        "SCORES",
        "GAME",
    }:
        return

    try:
        page.evaluate(
            """
            window.scrollTo(
                0,
                document.body.scrollHeight * 0.5
            )
            """
        )

        page.wait_for_timeout(500)

        page.evaluate(
            """
            window.scrollTo(
                0,
                document.body.scrollHeight
            )
            """
        )

        page.wait_for_timeout(700)

        page.evaluate(
            "window.scrollTo(0, 0)"
        )

    except Exception:
        pass


# ============================================================
# LINK EXTRACTION
# ============================================================

def get_all_links(page):
    links = set()

    for frame in page.frames:
        try:
            content = frame.content()
        except Exception:
            continue

        # Find all <a> tags
        anchors = re.findall(
            r'<a\b[^>]*>',
            content,
            re.IGNORECASE | re.DOTALL
        )

        for anchor in anchors:
            # Extract href attribute
            match = re.search(
                r'\bhref\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
                anchor,
                re.IGNORECASE
            )

            if not match:
                continue

            href = html.unescape(next(
                value for value in match.groups()
                if value is not None
            ))

            base_url = frame.url or page.url
            url = normalize_url(base_url, href)

            if url and is_mlb_url(url):
                links.add(url)

    return links

# ============================================================
# GAME LOGS
# ============================================================

def open_game_logs(page):
    """
    Try to switch a player profile into Game Logs.

    Returns True if something was successfully clicked.

    The caller then uses SPECIAL transition rules:
        GAME LOGS -> GAME
    """

    candidates = [
        page.get_by_text(
            "Game Logs",
            exact=True
        ),

        page.get_by_text(
            "Game Log",
            exact=True
        ),

        page.get_by_role(
            "link",
            name=re.compile(
                r"Game Logs?",
                re.IGNORECASE
            )
        ),

        page.get_by_role(
            "tab",
            name=re.compile(
                r"Game Logs?",
                re.IGNORECASE
            )
        ),
    ]

    for locator in candidates:

        try:
            count = locator.count()

            if count == 0:
                continue

            for index in range(
                min(count, 3)
            ):

                element = locator.nth(index)

                try:
                    if not element.is_visible(
                        timeout=700
                    ):
                        continue

                except Exception:
                    continue

                print(
                    "    [ACTION] Opening Game Logs"
                )

                before_url = page.url

                element.click(
                    timeout=4000
                )

                page.wait_for_timeout(
                    GAME_LOG_WAIT_MS
                )

                after_url = page.url

                if after_url != before_url:
                    print(
                        "    [GAME LOG URL]"
                    )
                    print(
                        f"    {after_url}"
                    )

                return True

        except Exception:
            continue

    return False


# ============================================================
# TRANSITIONS
# ============================================================

def allowed_transition(current_url, target_url, game_logs_open=False):
    """Keep old graph; add legitimate links into global stats and prospects.

    Do not allow ordinary PLAYER->PLAYER links. Players must appear on an
    actually visited stats/roster/scores/game/prospect page.
    """
    source_type = page_type(current_url)
    target_type = page_type(target_url)
    if url_key(current_url) == url_key(target_url):
        return False

    # These are genuine MLB sections, *only if they are actually linked*.
    # Every relevant section may lead toward large public stats directories.
    if target_type == "GLOBAL_STATS" and ENABLE_GLOBAL_STATS:
        return source_type in {
            "PLAYER", "STATS", "ROSTER", "SCORES", "GAME",
            "GLOBAL_STATS", "PROSPECTS",
        }
    if target_type == "PROSPECTS" and ENABLE_PROSPECTS:
        return source_type in {
            "PLAYER", "STATS", "ROSTER", "SCORES", "GAME",
            "GLOBAL_STATS", "PROSPECTS",
        }

    if source_type == "GLOBAL_STATS":
        return target_type in {"PLAYER", "STATS", "ROSTER"}
    if source_type == "PROSPECTS":
        return target_type in {"PLAYER", "STATS", "ROSTER"}

    if source_type == "PLAYER":
        if target_type in {"STATS", "ROSTER", "SCORES"}:
            return True
        if game_logs_open and target_type == "GAME":
            return True
        return False

    if source_type in {"STATS", "ROSTER"}:
        if target_type == "PLAYER":
            return True
        if target_type in {"STATS", "ROSTER", "SCORES"}:
            return same_team(current_url, target_url)
        return False

    if source_type == "SCORES":
        if target_type in {"PLAYER", "GAME"}:
            return True
        if target_type == "SCORES":
            first, second = team_slug(current_url), team_slug(target_url)
            return (first is None and second is None) or (
                first is not None and first == second
            )
        return False

    if source_type == "GAME":
        return target_type in {"PLAYER", "STATS", "ROSTER", "SCORES"}

    return False



# ============================================================
# PRIORITY
# ============================================================

def link_priority(current_url, target_url):
    # Players stay first, while league statistics get explored before
    # increasingly narrow score/date chains.
    return {
        "PLAYER": 0,
        "GLOBAL_STATS": 1,
        "PROSPECTS": 2,
        "GAME": 3,
        "ROSTER": 4,
        "STATS": 5,
        "SCORES": 6,
    }.get(page_type(target_url), 99)



# ============================================================
# EXPAND / RESTORE EXTENDED PLAYER BIO
# ============================================================

def expand_player_bio(page):
    """Expand the on-page narrative before saving rendered HTML/text.

    Returns True only if the button was actually clicked. A missing
    button is normal for some players and must not stop the crawler.
    """
    button = page.locator(
        "button.p-button__player-extended-bio"
    ).first

    try:
        if button.count() == 0:
            print("    [BIO] View More Bio Info button not found")
            return False
        button.wait_for(state="visible", timeout=1800)
    except TimeoutError:
        print("    [BIO] View More Bio Info button not found")
        return False
    except Exception as error:
        print(f"    [BIO] Cannot locate bio button: {error}")
        return False

    try:
        label = button.inner_text(timeout=2000).strip()

        # Some pages may already have the extra information expanded.
        if "view less" in label.lower() or "show less" in label.lower():
            print("    [BIO] Already expanded")
            return False

        button.scroll_into_view_if_needed(timeout=5000)
        button.click(timeout=7000)
        print("    [BIO] Clicked View More Bio Info +")

        # Wait for the button to change to its collapsed-state label,
        # or for an expanded state to be advertised by the website.
        # If MLB uses a different implementation, continue after the
        # bounded wait: saving should never be blocked by this check.
        try:
            page.wait_for_function(
                """() => {
                    const b = document.querySelector(
                        'button.p-button__player-extended-bio'
                    );
                    return !b || b.getAttribute('aria-expanded') === 'true'
                        || /view less|show less|collapse/i.test(b.textContent || '');
                }""",
                timeout=1800,
            )
        except TimeoutError:
            pass

        # Give any asynchronous biography request time to finish
        # before page.content() and body.inner_text() are called.
        page.wait_for_timeout(1200)
        return True

    except Exception as error:
        print(f"    [BIO WARNING] Could not expand biography: {error}")
        return False


def restore_player_bio(page):
    """Collapse the bio again so normal Game Logs navigation still works."""
    try:
        button = page.locator(
            "button.p-button__player-extended-bio"
        ).first
        if button.count() and button.is_visible():
            label = button.inner_text(timeout=1500).strip().lower()
            if (
                "view less" in label
                or "show less" in label
                or "collapse" in label
                or label.endswith("-")
            ):
                button.click(timeout=5000)
                return

        # If MLB presents a dialog instead of expanding inline,
        # close that dialog without changing the saved HTML/text.
        dialog = page.locator('[role="dialog"]:visible').first
        if dialog.count():
            close = dialog.locator(
                'button[aria-label="Close"], button[aria-label="close"]'
            ).first
            if close.count():
                close.click(timeout=4000)
            else:
                page.keyboard.press("Escape")
    except Exception as error:
        print(f"    [BIO WARNING] Could not restore collapsed view: {error}")


# ============================================================
# SAVE PLAYER
# ============================================================

def save_player(
    page,
    url,
    came_from
):
    pid = player_id(url)

    if not pid:
        return None

    player_dir = (
        OUTPUT_DIR
        / pid
    )

    # The directories/files may already exist from an earlier run.
    # write_text() below overwrites them with freshly rendered content.
    already_downloaded = (player_dir / "page.html").exists()

    player_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # IMPORTANT: expand the hidden narrative BEFORE saving the page.
    bio_was_expanded = expand_player_bio(page)

    # --------------------------------------------------------
    # HTML
    # --------------------------------------------------------

    try:
        html = page.content()

    except Exception:
        html = ""

    (
        player_dir
        / "page.html"
    ).write_text(
        html,
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    try:
        text = page.locator(
            "body"
        ).inner_text(
            timeout=5000
        )

    except Exception:
        text = ""

    (
        player_dir
        / "page.txt"
    ).write_text(
        text,
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # NAME
    # --------------------------------------------------------

    try:
        name = page.locator(
            "h1"
        ).first.inner_text(
            timeout=3000
        )

    except Exception:
        name = "Unknown"

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    metadata = {
        "player_id": pid,
        "name": name,
        "url": url,
        "discovered_from": came_from,
        "discovered_from_type": (
            page_type(came_from)
            if came_from
            else None
        ),
    }

    (
        player_dir
        / "metadata.json"
    ).write_text(
        json.dumps(
            metadata,
            indent=4,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    status = "UPDATED" if already_downloaded else "SAVED"
    print(f"    [PLAYER {status}] {name} ({pid})")

    # Restore the original view after the *expanded* HTML/text have
    # already been saved. The crawling rules/queue remain unchanged.
    if bio_was_expanded:
        restore_player_bio(page)

    return name


# ============================================================
# GRAPH LOG
# ============================================================

def initialize_graph_file():
    graph_file = (
        OUTPUT_DIR
        / "crawl_graph.csv"
    )

    if not graph_file.exists():

        with graph_file.open(
            "w",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.writer(
                file
            )

            writer.writerow(
                [
                    "source_url",
                    "source_type",
                    "target_url",
                    "target_type",
                ]
            )

    return graph_file


def record_edge(
    graph_file,
    source_url,
    target_url
):
    with graph_file.open(
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(
            file
        )

        writer.writerow(
            [
                source_url,
                page_type(source_url),
                target_url,
                page_type(target_url),
            ]
        )



# ============================================================
# INTERACTIVE DISCOVERY (PAGINATION / HISTORICAL SEASONS)
# ============================================================

def table_signature(page):
    """Detect an actual table change, not just a click on a disabled button."""
    try:
        return page.evaluate("""() => {
            const tables = [...document.querySelectorAll('table')];
            const t = tables.find(x => x.querySelector('a[href*="/player/"]'))
                || tables[0];
            if (t) return (location.href + '|' + (t.innerText || '').slice(0, 15000));
            const anchors = [...document.querySelectorAll('a[href*="/player/"]')]
                .map(a => a.href);
            return location.href + '|' + anchors.join('|');
        }""")
    except Exception:
        return page.url


def next_table_button(page):
    """Return an *existing* clickable page-next control, if found."""
    selectors = [
        'a[rel="next"]',
        'button[aria-label="Next Page" i]',
        'a[aria-label="Next Page" i]',
        'button[aria-label="Next" i]',
        'a[aria-label="Next" i]',
        '.p-pagination__next button',
        '.pagination__next button',
        '.pagination__next a',
        '[data-testid="pagination-next"]',
    ]
    for selector in selectors:
        try:
            for button in page.locator(selector).all()[:5]:
                if not button.is_visible(timeout=400) or not button.is_enabled():
                    continue
                if button.get_attribute("aria-disabled") == "true":
                    continue
                return button
        except Exception:
            pass
    # Conservative role-based fallback: not generic carousel arrows.
    for role in ("button", "link"):
        try:
            for button in page.get_by_role(
                role, name=re.compile(r"^Next(?: Page)?$", re.I)
            ).all()[:3]:
                if button.is_visible(timeout=400) and button.is_enabled():
                    if button.get_attribute("aria-disabled") != "true":
                        return button
        except Exception:
            pass
    return None


def collect_paginated_links(page, kind):
    """Return (target URL, observed source URL) pairs from actual UI views.

    Only MLB anchor hrefs are extracted. The Next action must be an
    enabled button/link actually present in the rendered page.
    """
    discovered = {}
    for link in get_all_links(page):
        discovered.setdefault(link, page.url)

    if not ENABLE_INTERACTIVE_PAGINATION or kind not in {
        "STATS", "GLOBAL_STATS", "PROSPECTS", "ROSTER"
    }:
        return discovered

    known_signatures = {table_signature(page)}
    steps = 0
    for _ in range(MAX_TABLE_PAGES_PER_URL - 1):
        button = next_table_button(page)
        if button is None:
            break
        try:
            before = table_signature(page)
            button.scroll_into_view_if_needed(timeout=1800)
            button.click(timeout=3500)
            # Wait for AJAX table replacement or real navigation to complete.
            for _ in range(12):
                page.wait_for_timeout(300)
                if table_signature(page) != before:
                    break
            after = table_signature(page)
            if after == before or after in known_signatures:
                break
            known_signatures.add(after)
            steps += 1
            for link in get_all_links(page):
                discovered.setdefault(link, page.url)
            # Don't rely on an invented next-page URL.
        except Exception as exc:
            print(f"    [PAGINATION] stopped: {type(exc).__name__}")
            break
    if steps:
        print(f"    [PAGINATION] clicked {steps} real Next controls")
    return discovered


def native_season_options(page):
    """Find real native year options; do not synthesize season URLs."""
    try:
        return page.locator("select").evaluate_all("""els =>
            els.map((el, idx) => ({
                idx,
                options: [...el.options].map(o => ({
                    value: o.value,
                    label: (o.textContent || '').trim()
                }))
            }))
        """)
    except Exception:
        return []


def discovered_season_options(page):
    result = []
    for entry in native_season_options(page):
        years = []
        for option in entry.get("options", []):
            label = option.get("label", "")
            match = re.fullmatch(r"\s*(18\d{2}|19\d{2}|20\d{2})\s*", label)
            if not match:
                continue
            year = int(match.group(1))
            if MIN_SEASON <= year <= datetime.now(timezone.utc).year:
                years.append((year, option.get("value", "")))
        if len(years) >= 5:
            years.sort(reverse=True)
            result.append((entry["idx"], years))
    return result


def click_season_options(page, initial_url):
    """Explore years ONLY offered by a real native MLB selection widget.

    This runs only on the league-wide stats landing views, never on each
    season page. It records actual URLs reached after selecting options.
    """
    discovered = {}
    if not ENABLE_SEASON_SELECTION or not is_global_stats_url(initial_url):
        return discovered
    parts = path_parts(initial_url)
    # Avoid reopening season selectors on every /stats/2025 or /stats/pitching/2025.
    if parts not in (["stats"], ["stats", "pitching"]):
        return discovered

    candidates = discovered_season_options(page)
    if not candidates:
        return click_custom_season_menu(page, initial_url)
    index, choices = max(candidates, key=lambda pair: len(pair[1]))
    chosen = choices[:MAX_SEASON_CHOICES]
    print(f"    [SEASONS] {len(chosen)} actual year options detected")
    for year, value in chosen:
        try:
            if page.url != initial_url:
                page.goto(initial_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(500)
            # Select via the actual DOM widget; MLB decides target URL.
            page.locator("select").nth(index).select_option(value=value, timeout=3000)
            page.wait_for_timeout(700)
            arrived = page.url
            if url_key(arrived) != url_key(initial_url):
                discovered.setdefault(arrived, initial_url)
            else:
                # Some selectors update only an AJAX table.
                for link in get_all_links(page):
                    discovered.setdefault(link, page.url)
            print(f"    [SEASONS] clicked {year} -> {arrived}")
        except Exception as exc:
            print(f"    [SEASONS] {year} unavailable: {type(exc).__name__}")

    # Restore the original view for the outer crawl loop.
    try:
        if page.url != initial_url:
            page.goto(initial_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(500)
    except Exception:
        pass
    return discovered


def click_custom_season_menu(page, initial_url):
    """Fallback for MLB pages using a JavaScript year dropdown instead of <select>.

    Both the year trigger and the year options must be present in the DOM.
    No guessed navigation addresses are generated.
    """
    discovered = {}
    triggers = [
        lambda: page.get_by_role("combobox", name=re.compile(r"season|year", re.I)),
        lambda: page.locator('button[aria-label*="year" i], button[aria-label*="season" i]'),
        lambda: page.locator('button[aria-haspopup="listbox"]')
            .filter(has_text=re.compile(r"^\s*(18|19|20)\d{2}\s*$")),
        lambda: page.get_by_role('button', name=re.compile(r"^(18|19|20)\d{2}$")),
    ]

    def find_trigger():
        for factory in triggers:
            try:
                items = factory()
                for i in range(min(items.count(), 8)):
                    item = items.nth(i)
                    if item.is_visible(timeout=350) and item.is_enabled():
                        return item
            except Exception:
                pass
        return None

    def open_options():
        trigger = find_trigger()
        if trigger is None:
            return []
        try:
            trigger.click(timeout=1800)
            page.wait_for_timeout(400)
            opts = page.get_by_role("option")
            found = []
            for i in range(min(opts.count(), 250)):
                option = opts.nth(i)
                if not option.is_visible(timeout=300):
                    continue
                label = option.inner_text(timeout=500).strip()
                match = re.fullmatch(r"(18\d{2}|19\d{2}|20\d{2})", label)
                if match:
                    year = int(match.group(1))
                    if MIN_SEASON <= year <= datetime.now(timezone.utc).year:
                        found.append(year)
            return sorted(set(found), reverse=True)
        except Exception:
            return []

    years = open_options()
    if len(years) < 5:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        print("    [SEASONS] No usable custom year menu detected")
        return discovered

    print(f"    [SEASONS] {min(len(years), MAX_SEASON_CHOICES)} custom year choices")
    # close the discovery menu: open_options() must reopen it for each year.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass
    for year in years[:MAX_SEASON_CHOICES]:
        try:
            if page.url != initial_url:
                page.goto(initial_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(450)
            # Reopen menu for each choice (a selection closes it).
            fresh_years = open_options()
            if year not in fresh_years:
                continue
            option = page.get_by_role("option", name=str(year), exact=True).first
            option.click(timeout=2400)
            page.wait_for_timeout(750)
            arrived = page.url
            if url_key(arrived) != url_key(initial_url):
                discovered.setdefault(arrived, initial_url)
            else:
                for link in get_all_links(page):
                    discovered.setdefault(link, page.url)
            print(f"    [SEASONS] selected {year} -> {arrived}")
        except Exception as exc:
            print(f"    [SEASONS] custom {year} failed: {type(exc).__name__}")

    try:
        page.goto(initial_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(500)
    except Exception:
        pass
    return discovered



# ============================================================
# RESTARTABLE QUEUE
# ============================================================

def save_checkpoint(queue, queued, visited, saved_players, pages_processed,
                    page_type_counts):
    data = {
        "version": STATE_VERSION,
        "start_url": START_URL,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "queue": list(queue),
        "queued": sorted(queued),
        "visited": sorted(visited),
        "saved_players": sorted(saved_players),
        "pages_processed": pages_processed,
        "page_type_counts": dict(page_type_counts),
    }
    temp = STATE_FILE.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(STATE_FILE)


def load_checkpoint():
    if not RESUME or not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if data.get("version") != STATE_VERSION or data.get("start_url") != START_URL:
            print("[STATE] Checkpoint differs from current version/start; starting fresh")
            return None
        print(f"[STATE] Resuming {len(data['queue'])} URLs, "
              f"{len(data['saved_players'])} players, "
              f"{data['pages_processed']} pages")
        return data
    except (ValueError, KeyError, OSError) as exc:
        print(f"[STATE] Could not load checkpoint ({exc}); starting fresh")
        return None

# ============================================================
# FALLBACK ROOTS
# ============================================================

def enqueue_next_fallback_seed(queue, queued, visited):
    """Add the first unvisited root when the existing crawl is exhausted.

    Visited/queued players use the SAME canonical URL keys as normal crawling.
    Nothing is added to the crawl graph as a fabricated hyperlink.
    """
    for seed in FALLBACK_START_URLS:
        seed = seed.strip()
        if not seed or not is_mlb_url(seed):
            print(f"[SEED SKIP] Not an MLB URL: {seed!r}")
            continue

        key = url_key(seed)
        if key in visited or key in queued:
            continue

        queue.append((seed, None))
        queued.add(key)
        print("\n" + "=" * 70)
        print(f"[NEW SEED] Frontier exhausted. Starting: {seed}")
        return True

    return False


# ============================================================
# CRAWLER
# ============================================================

def crawl():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    graph_file = initialize_graph_file()

    # --------------------------------------------------------
    # QUEUE
    #
    # (
    #     URL,
    #     URL where this URL was discovered
    # )
    # --------------------------------------------------------

    queue = deque(
        [
            (
                START_URL,
                None,
            )
        ]
    )

    queued = {
        url_key(START_URL)
    }

    visited = set()

    saved_players = set()

    pages_processed = 0

    page_type_counts = defaultdict(
        int
    )

    checkpoint = load_checkpoint()
    if checkpoint:
        queue = deque(tuple(pair) for pair in checkpoint["queue"])
        queued = set(checkpoint["queued"])
        visited = set(checkpoint["visited"])
        saved_players = set(checkpoint["saved_players"])
        pages_processed = checkpoint["pages_processed"]
        page_type_counts.update(checkpoint.get("page_type_counts", {}))
    elif STATE_FILE.exists() and not RESUME:
        print("[STATE] Resume disabled; starting fresh")

    # --------------------------------------------------------
    # PLAYWRIGHT
    # --------------------------------------------------------

    with sync_playwright() as playwright:

        browser = playwright.chromium.launch(
            headless=False
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            }
        )

        page = context.new_page()

        # ====================================================
        # MAIN LOOP
        # ====================================================

        while (
            pages_processed < MAX_PAGES
            and len(saved_players) < MAX_PLAYERS
        ):

            # Keep the existing crawler working until no links remain.
            # Then seamlessly start from the next unused predefined seed.
            # This also works when restoring an old checkpoint whose queue
            # is already empty (e.g. after the previous 557-player run).
            if not queue:
                if not enqueue_next_fallback_seed(queue, queued, visited):
                    print("[SEEDS] No unvisited fallback URLs remain.")
                    break

                # Persist the new root immediately, so resume won't lose it.
                save_checkpoint(queue, queued, visited, saved_players,
                                pages_processed, page_type_counts)

            url, came_from = (
                queue.popleft()
            )

            requested_key = url_key(
                url
            )

            queued.discard(
                requested_key
            )

            if requested_key in visited:
                continue

            print()
            print("=" * 70)

            print("[REQUEST]")
            print(url)

            if came_from:

                print(
                    "[DISCOVERED FROM]"
                )

                print(
                    came_from
                )

            # ==================================================
            # OPEN PAGE
            # ==================================================

            try:

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=20000,
                )

            except TimeoutError:

                print(
                    "[WARNING] navigation timeout"
                )

            except Exception as error:

                print(
                    "[ERROR] navigation failed:"
                )

                print(
                    error
                )

                visited.add(
                    requested_key
                )

                continue

            # ==================================================
            # REDIRECT
            # ==================================================

            current_url = page.url

            current_key = url_key(
                current_url
            )

            # If the short URL redirected to a player/page
            # that was already processed, skip it.
            if (
                current_key in visited
                and current_key != requested_key
            ):

                visited.add(
                    requested_key
                )

                print(
                    "[SKIP] Redirected to already visited page"
                )

                continue

            visited.add(
                requested_key
            )

            visited.add(
                current_key
            )

            pages_processed += 1

            current_type = page_type(
                current_url
            )

            page_type_counts[
                current_type
            ] += 1

            print(
                f"[PAGE {pages_processed}]"
            )

            print(
                current_url
            )

            print(
                f"[TYPE] {current_type}"
            )

            # ==================================================
            # LET CONTENT RENDER
            # ==================================================

            settle_page(
                page,
                current_type
            )

            # ==================================================
            # PLAYER
            # ==================================================

            game_logs_open = False

            if current_type == "PLAYER":

                pid = player_id(
                    current_url
                )

                if (
                    pid
                    and pid not in saved_players
                ):

                    save_player(
                        page,
                        current_url,
                        came_from,
                    )

                    saved_players.add(
                        pid
                    )

                # First collect links BEFORE Game Logs.
                try:

                    normal_links = get_all_links(
                        page
                    )

                except Exception:

                    normal_links = set()

                # Then open Game Logs.
                game_logs_open = open_game_logs(
                    page
                )

                if game_logs_open:

                    # Scroll again because table may be lazy.
                    try:

                        page.evaluate(
                            """
                            window.scrollTo(
                                0,
                                document.body.scrollHeight
                            )
                            """
                        )

                        page.wait_for_timeout(
                            800
                        )

                    except Exception:
                        pass

                # Extract links after Game Logs too.
                try:

                    after_game_log_links = (
                        get_all_links(page)
                    )

                except Exception:

                    after_game_log_links = set()

                links = (
                    normal_links
                    | after_game_log_links
                )

            # ==================================================
            # NON-PLAYER PAGE
            # ==================================================

            else:

                try:

                    links = get_all_links(
                        page
                    )

                except Exception as error:

                    print(
                        "[ERROR] link extraction failed:"
                    )

                    print(
                        error
                    )

                    continue

            # ==================================================
            # PAGINATION AND HISTORIC-SEASON UI
            # ==================================================

            # Standard player behavior above is unchanged. For table pages,
            # collect every player anchor from real Next-button pages too.
            link_origins = {link: current_url for link in links}
            if current_type in {"GLOBAL_STATS", "STATS", "PROSPECTS", "ROSTER"}:
                paginated = collect_paginated_links(page, current_type)
                for link, actual_source in paginated.items():
                    link_origins.setdefault(link, actual_source)
                # Season selector is checked on league-wide stats landing page.
                # After UI pagination, restore the landing URL before selecting.
                if current_type == "GLOBAL_STATS" and ENABLE_SEASON_SELECTION:
                    if page.url != current_url:
                        try:
                            page.goto(current_url, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(500)
                        except Exception:
                            pass
                    year_links = click_season_options(page, current_url)
                    for link, actual_source in year_links.items():
                        link_origins.setdefault(link, actual_source)
            links = set(link_origins)

            # ==================================================
            # DEBUG
            # ==================================================

            print(
                f"[FRAMES] {len(page.frames)}"
            )

            print(
                f"[LINKS] "
                f"{len(links)} MLB links found"
            )

            # ==================================================
            # FILTER LINKS
            # ==================================================

            useful_links = []
            rejected = Counter()

            for link in links:

                if not is_mlb_url(link):
                    rejected["outside_mlb"] += 1
                    continue

                target_key = url_key(
                    link
                )

                if target_key in visited:
                    rejected["visited"] += 1
                    continue

                if target_key in queued:
                    rejected["queued"] += 1
                    continue

                source_for_link = link_origins.get(link, current_url)
                # UI-generated year landing URLs are real navigations we
                # performed by selecting an existing option. The original
                # stats page remains the proven origin for them.
                if not allowed_transition(
                    source_for_link,
                    link,
                    game_logs_open=game_logs_open,
                ):
                    rejected["transition"] += 1
                    continue

                useful_links.append(
                    link
                )

            # ==================================================
            # SORT
            # ==================================================

            useful_links.sort(
                key=lambda link: (
                    link_priority(
                        current_url,
                        link
                    ),
                    link,
                )
            )

            print(
                f"[USEFUL] "
                f"{len(useful_links)} candidate links"
            )

            if PRINT_DISCOVERY_DIAGNOSTICS:
                print("[FILTER] " + " ".join(
                    f"{reason}={count}" for reason, count in sorted(rejected.items())
                ))

            # ==================================================
            # SHOW LINKS + QUEUE
            # ==================================================

            for link in useful_links:

                target_type = page_type(
                    link
                )

                print(
                    f"    -> "
                    f"{target_type:7} "
                    f"{link}"
                )

                record_edge(
                    graph_file,
                    link_origins.get(link, current_url),
                    link,
                )

                target_key = url_key(
                    link
                )

                if target_key in visited:
                    continue

                if target_key in queued:
                    continue

                queue.append(
                    (
                        link,
                        link_origins.get(link, current_url),
                    )
                )

                queued.add(
                    target_key
                )

            # ==================================================
            # STATUS
            # ==================================================

            print(
                f"[QUEUE] "
                f"{len(queue)} pages waiting"
            )

            print(
                f"[PLAYERS] "
                f"{len(saved_players)}/{MAX_PLAYERS}"
            )

            print(
                "[VISITED TYPES] "
                + " ".join(
                    f"{key}={value}"
                    for key, value
                    in sorted(
                        page_type_counts.items()
                    )
                )
            )

            save_checkpoint(queue, queued, visited, saved_players,
                            pages_processed, page_type_counts)
            time.sleep(REQUEST_DELAY)

        # ====================================================
        # END
        # ====================================================

        save_checkpoint(queue, queued, visited, saved_players,
                        pages_processed, page_type_counts)
        browser.close()

    print()
    print("=" * 70)
    print("CRAWLER FINISHED")
    print("=" * 70)

    print(
        f"Pages processed: "
        f"{pages_processed}"
    )

    print(
        f"Players saved: "
        f"{len(saved_players)}"
    )

    print(
        f"Queue remaining: "
        f"{len(queue)}"
    )

    print()

    print(
        "Visited page types:"
    )

    for kind, count in sorted(
        page_type_counts.items()
    ):

        print(
            f"  {kind}: {count}"
        )

    print()

    print(
        "Graph saved to:"
    )

    print(
        graph_file
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    crawl()