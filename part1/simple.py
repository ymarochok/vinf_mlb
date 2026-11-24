#!/usr/bin/env python3
"""MLB All-Time Totals -> player profiles. No link-to-link crawling.

Existing player directories are untouched. The original save_player(),
expand_player_bio(), and restore_player_bio() functions are copied verbatim
from mlb_crawler_all_time_pagination.py.
"""

from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import csv
import json
import re
import time

# ============================================================
# CONFIGURATION
# ============================================================

ALL_TIME_URL = "https://www.mlb.com/stats/all-time-totals"
OUTPUT_DIR = Path("stored_players")  # SAME folder as your previous crawler
STATE_FILE = OUTPUT_DIR / "all_time_only_state.json"  # NEW checkpoint; old one untouched
GRAPH_FILE = OUTPUT_DIR / "crawl_graph.csv"  # SAME graph format as before
FAILED_FILE = OUTPUT_DIR / "all_time_failed_players.json"

MAX_LEADERBOARD_PAGES = 1200   # hard ceiling, not a stopping target
MIN_EXPECTED_PAGES = 849     # from the site's page counter observed by user
PAGE_DELAY_SECONDS = 0.5
PLAYER_DELAY_SECONDS = 0.4
PROFILE_LOAD_WAIT_MS = 750
NEXT_CHANGE_TIMEOUT_MS = 10000
MAX_RETRIES = 4
HEADLESS = False

PLAYER_PATH_RE = re.compile(r"^/player/(?:[^/?#]+-)?(\d+)/?$")


def player_id(url):
    path = urlparse(url).path.rstrip("/")
    match = PLAYER_PATH_RE.match(path)
    return match.group(1) if match else None


def page_type(url):
    # Kept for unchanged save_player() metadata.
    # This crawler supplies only ALL_TIME_URL pages as discovered_from.
    if player_id(url):
        return "PLAYER"
    if urlparse(url).path.startswith("/stats/") or urlparse(url).path == "/stats":
        return "GLOBAL_STATS"
    return "OTHER"


def player_already_downloaded(pid):
    """Skip only if all three original files have actual content."""
    directory = OUTPUT_DIR / pid
    files = (directory / "page.html", directory / "page.txt", directory / "metadata.json")
    return all(f.is_file() and f.stat().st_size > 0 for f in files)


def read_json(path, default):
    if not path.exists():
        return default
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, type(default)) else default
    except (OSError, ValueError) as exc:
        print(f"[WARNING] Cannot load {path}: {exc}")
        return default


def write_json_atomic(path, data):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def initialize_graph_file():
    if not GRAPH_FILE.exists():
        with GRAPH_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["source_url", "source_type", "target_url", "target_type"])


def record_edge(source, target):
    with GRAPH_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([source, page_type(source), target, page_type(target)])


# ============================================================
# ORIGINAL PLAYER-SAVING CODE (UNCHANGED)
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
# EXTRACT ONLY PLAYER LINKS FROM THE LEADERBOARD TABLE
# ============================================================

def leaderboard_players(page):
    """Read actual MLB profile hrefs in the stats table, not site navigation."""
    selectors = (
        'table a[href*="/player/"]',
        '[role="table"] a[href*="/player/"]',
        '.stats-table a[href*="/player/"]',
    )
    links = []
    for selector in selectors:
        try:
            links = page.locator(selector).evaluate_all("els => els.map(a => a.href)")
            if links:
                break
        except Exception:
            continue
    result = OrderedDict()
    for href in links:
        pid = player_id(href)
        if pid and urlparse(href).hostname in {"www.mlb.com", "mlb.com"}:
            result.setdefault(pid, href)
    return result


def page_signature(players):
    # Do not count URL changes as successful pagination: rows must change.
    return tuple(players.keys())


def leaderboard_page_url(number):
    # MLB itself serves this genuine pagination pattern (e.g., ?page=3).
    return ALL_TIME_URL if number == 1 else f"{ALL_TIME_URL}?page={number}"


def load_leaderboard(page, number, previous_signature=()):
    """Open a real MLB numbered page, retry and verify it isn't a duplicate."""
    target = leaderboard_page_url(number)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30000)
            # The stats table is client-rendered, and often takes extra time.
            page.wait_for_function(
                """() => !!document.querySelector(
                    'table a[href*="/player/"], '
                    + '[role="table"] a[href*="/player/"], '
                    + '.stats-table a[href*="/player/"]'
                )""",
                timeout=16000,
            )
            page.wait_for_timeout(450)
            players = leaderboard_players(page)
            signature = page_signature(players)
            if signature and signature != previous_signature:
                return players
            print(f"[LEADERBOARD] Page {number}: empty or repeated player rows "
                  f"(attempt {attempt}/{MAX_RETRIES})")
        except Exception as error:
            print(f"[LEADERBOARD] Page {number}: {type(error).__name__} "
                  f"(attempt {attempt}/{MAX_RETRIES})")
        if attempt < MAX_RETRIES:
            page.wait_for_timeout(900 * attempt)
    return None


def next_is_explicitly_disabled(page):
    """Recognize a real, visible disabled stats-pagination Next control."""
    for selector in (
        'button[aria-label="next page button" i]',
        '.bui-button-group.pagination button[aria-label*="next" i]',
        'button[aria-label*="next page" i]',
    ):
        try:
            elements = page.locator(selector)
            for i in range(min(elements.count(), 5)):
                button = elements.nth(i)
                if button.is_visible(timeout=350):
                    return (not button.is_enabled()
                            or button.get_attribute("aria-disabled") == "true")
        except Exception:
            continue
    return False


def next_button(page):
    """Find MLB's actual Next page button, avoiding unrelated carousels."""
    selectors = (
        'button[aria-label="next page button" i]',
        '.bui-button-group.pagination button[aria-label*="next" i]',
        'button[aria-label*="next page" i]',
        'a[aria-label*="next page" i]',
        'a[rel="next"]',
    )
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 4)):
                button = loc.nth(i)
                if button.is_visible(timeout=500) and button.is_enabled():
                    if button.get_attribute("aria-disabled") != "true":
                        return button
        except Exception:
            continue
    return None


def click_next_or_recover(page, next_number, previous_signature):
    """Click real Next. If stale/missing, use verified MLB ?page=N recovery."""
    button = next_button(page)
    if button is not None:
        try:
            button.scroll_into_view_if_needed(timeout=3500)
            button.click(timeout=6500)
            elapsed = 0
            while elapsed < NEXT_CHANGE_TIMEOUT_MS:
                page.wait_for_timeout(400)
                elapsed += 400
                players = leaderboard_players(page)
                if players and page_signature(players) != previous_signature:
                    return players
            print(f"[NEXT] Page {next_number}: click produced no new rows; "
                  "trying numbered URL")
        except Exception as error:
            print(f"[NEXT] Page {next_number}: {type(error).__name__}; "
                  "trying numbered URL")
    else:
        print(f"[NEXT] Page {next_number}: button unavailable; "
              "trying numbered URL")

    return load_leaderboard(page, next_number, previous_signature)


# ============================================================
# INCREMENTAL STATE: DISCOVERY AND PROFILE DOWNLOADS
# ============================================================

def initial_state():
    return {
        "version": 1,
        "leaderboard": ALL_TIME_URL,
        "next_page": 1,
        "pending": [],
        "previous_signature": [],
        "downloaded_this_run": 0,
        "pages_scanned": 0,
        "completed": False,
    }


def checkpoint(state):
    state["updated_utc"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(STATE_FILE, state)


def download_pending(page, state, failures):
    """Process saved queue of real profile hrefs; don't crawl their links."""
    while state["pending"]:
        entry = state["pending"][0]
        url, source = entry["url"], entry["source"]
        pid = player_id(url)
        if not pid or player_already_downloaded(pid):
            state["pending"].pop(0)
            checkpoint(state)
            continue

        success = False
        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(PROFILE_LOAD_WAIT_MS)
                actual_pid = player_id(page.url)
                if actual_pid != pid:
                    raise RuntimeError(f"Unexpected redirect: {page.url}")
                # Do not save a temporary blank or blocked page as a player.
                page.locator("h1").first.wait_for(state="visible", timeout=10000)
                save_player(page, page.url, source)  # ORIGINAL SAVING FUNCTION
                if not player_already_downloaded(pid):
                    raise RuntimeError("Player files were empty/incomplete after saving")
                success = True
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                print(f"    [PLAYER RETRY] {pid} ({attempt}/{MAX_RETRIES}): "
                      f"{last_error[:160]}")
                if attempt < MAX_RETRIES:
                    page.wait_for_timeout(attempt * 800)

        if success:
            state["downloaded_this_run"] += 1
            failures.pop(pid, None)
            print(f"[DOWNLOADED] {pid} | run={state['downloaded_this_run']}")
        else:
            failures[pid] = {"url": url, "source": source, "error": last_error}
            print(f"[FAILED] {pid}; recorded for a later retry")

        state["pending"].pop(0)
        # Crash-safe per player; no previously downloaded profile is overwritten.
        checkpoint(state)
        write_json_atomic(FAILED_FILE, failures)
        time.sleep(PLAYER_DELAY_SECONDS)


def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    initialize_graph_file()
    state = read_json(STATE_FILE, initial_state())
    if state.get("version") != 1 or state.get("leaderboard") != ALL_TIME_URL:
        raise RuntimeError(f"Unsupported checkpoint in {STATE_FILE}; back it up first")
    failures = read_json(FAILED_FILE, {})

    if state["completed"] and not state["pending"]:
        print("[DONE] Leaderboard already completed. Existing files untouched.")
        return

    print(f"[START] Only leaderboard: {ALL_TIME_URL}")
    print(f"[RESUME] Page {state['next_page']}, "
          f"{len(state['pending'])} queued profiles, "
          f"{state['downloaded_this_run']} downloaded in this job")
    print("[SKIP] Previously downloaded players are checked by ID on disk")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        leaderboard_page = context.new_page()
        profile_page = context.new_page()  # browsing profiles can't disrupt pagination
        try:
            # Process pending from last interrupted page first.
            download_pending(profile_page, state, failures)
            # Failed profiles from a prior run deserve another chance;
            # their identifiers are checked against the disk again.
            if failures:
                for pid, entry in list(failures.items()):
                    if not player_already_downloaded(pid):
                        state["pending"].append({
                            "url": entry["url"], "source": entry["source"]
                        })
                checkpoint(state)
                download_pending(profile_page, state, failures)
            number = state["next_page"]
            prev_signature = tuple(state.get("previous_signature", []))

            if number > MAX_LEADERBOARD_PAGES:
                raise RuntimeError("Reached MAX_LEADERBOARD_PAGES; raise this limit")

            players = load_leaderboard(leaderboard_page, number, prev_signature)
            if not players:
                print(f"[PAUSED] Cannot verify leaderboard page {number}; "
                      "checkpoint retained. NOT marked complete.")
                return

            while number <= MAX_LEADERBOARD_PAGES:
                # Current page was confirmed distinct from the previous one.
                # Re-check disk *before enqueue*, as requested.
                new_players = []
                duplicates = 0
                for pid, url in players.items():
                    if player_already_downloaded(pid):
                        duplicates += 1
                        continue
                    if any(player_id(item["url"]) == pid for item in state["pending"]):
                        duplicates += 1
                        continue
                    new_players.append({
                        "url": url,
                        "source": leaderboard_page_url(number),
                    })

                state["pending"].extend(new_players)
                # Save the page's candidate queue before loading any profiles.
                checkpoint(state)
                for entry in new_players:
                    record_edge(entry["source"], entry["url"])
                print(f"[ALL-TIME PAGE {number}] {len(players)} player links | "
                      f"already downloaded={duplicates} | new={len(new_players)}")

                download_pending(profile_page, state, failures)

                # Page is fully handled. Persist the index of the *next* page.
                signature = page_signature(players)
                state["previous_signature"] = list(signature)
                state["next_page"] = number + 1
                state["pages_scanned"] += 1
                checkpoint(state)
                print(f"[PROGRESS] Next page {state['next_page']} | "
                      f"downloaded in job {state['downloaded_this_run']} | "
                      f"failed profiles {len(failures)}")

                # Check the *current* final page before attempting to open
                # page N+1; otherwise the browser moves away from the disabled
                # Next control and we could miss the natural end.
                if next_is_explicitly_disabled(leaderboard_page):
                    if number >= MIN_EXPECTED_PAGES:
                        state["completed"] = True
                        checkpoint(state)
                        print(f"[COMPLETE] MLB disabled Next on page {number}")
                    else:
                        print(f"[PAUSED] Next disabled early on page {number}; "
                              f"expected at least {MIN_EXPECTED_PAGES}. "
                              "Check filters/site behavior; state retained.")
                    return

                if number >= MAX_LEADERBOARD_PAGES:
                    print(f"[PAUSED] Safety ceiling of {MAX_LEADERBOARD_PAGES} reached")
                    return

                next_number = number + 1
                players = click_next_or_recover(leaderboard_page, next_number, signature)
                if not players:
                    # A temporary network/DOM failure must NEVER be mistaken
                    # for the last page of a 849-page table.
                    at_real_end = next_is_explicitly_disabled(leaderboard_page)
                    if number >= MIN_EXPECTED_PAGES and at_real_end:
                        print(f"[LEADERBOARD COMPLETE] Last Next control disabled "
                              f"after page {number}")
                        state["completed"] = True
                        checkpoint(state)
                    else:
                        print(f"[PAUSED] Could not confirm page {next_number}. "
                              f"Last verified page was {number}. "
                              "Checkpoint retained; run again to retry.")
                    return
                number = next_number
                time.sleep(PAGE_DELAY_SECONDS)
        except KeyboardInterrupt:
            checkpoint(state)
            print("\n[INTERRUPTED] State saved; rerun to continue")
        finally:
            write_json_atomic(FAILED_FILE, failures)
            context.close()
            browser.close()


if __name__ == "__main__":
    run()
