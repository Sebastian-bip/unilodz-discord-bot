#!/usr/bin/env python3
"""Scraper aktualności i wydarzeń z serwisów Uniwersytetu Łódzkiego (WFiIS UŁ i UŁ)
publikujący nowe wpisy na kanale Discord przez Discord Webhook.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("unilodz_scraper")

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "seen_posts.json"
ENV_FILE = BASE_DIR / ".env"

REQUEST_TIMEOUT = 20
WEBHOOK_DELAY_SECONDS = 1.0
MAX_EMBED_TITLE = 256
MAX_EMBED_DESCRIPTION = 400

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

TRACKING_PARAMS_PREFIXES = ("utm_",)
TRACKING_PARAMS_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass(frozen=True)
class SourceConfig:
    key: str
    name: str
    url: str
    color: int


SOURCES: list[SourceConfig] = [
    SourceConfig(
        key="wfis",
        name="WFiIS UŁ",
        url="https://www.wfis.uni.lodz.pl/strefa-studenta/",
        color=0x005CA9,  # Niebieski (WFiIS UŁ)
    ),
    SourceConfig(
        key="unilodz",
        name="UŁ",
        url="https://www.uni.lodz.pl/studiuje-w-unilodz",
        color=0xC8102E,  # Karmazynowy / czerwony (Uniwersytet Łódzki)
    ),
]


@dataclass
class Post:
    post_id: str
    title: str
    url: str
    source_name: str
    source_url: str
    color: int
    date: str
    description: str
    thumbnail_url: str | None
    category: str | None = None


def load_dotenv(env_path: Path = ENV_FILE) -> None:
    """Wczytuje zmienne środowiskowe z lokalnego pliku .env (jeśli istnieje)."""
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as exc:
        logger.warning("Nie udało się odczytać pliku .env: %s", exc)


def clean_text(text: str | None) -> str:
    """Normalizuje białe znaki w tekście."""
    if not text:
        return ""
    return " ".join(text.split()).strip()


def truncate(text: str, max_len: int) -> str:
    """Skraca tekst do limitu znaków z wielokropkiem."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def normalize_url(raw_url: str, base_url: str) -> str:
    """Zamienia URL względny na absolutny i usuwa parametry śledzące (utm_*, fbclid)."""
    absolute = urljoin(base_url, raw_url.strip())
    parts = urlsplit(absolute)
    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS_EXACT
        and not any(k.lower().startswith(p) for p in TRACKING_PARAMS_PREFIXES)
    ]
    clean_query = urlencode(filtered_query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, clean_query, ""))


def load_seen_posts(path: Path = SEEN_FILE) -> dict[str, dict[str, str]]:
    """Wczytuje mapę już wysłanych wpisów z pliku seen_posts.json."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Błąd odczytu %s (%s) – startuję z pustą bazą.", path.name, exc)
        return {}

    if isinstance(data, list):
        return {str(item): {"seen_at": ""} for item in data}
    if isinstance(data, dict):
        return {str(k): v if isinstance(v, dict) else {"seen_at": str(v)} for k, v in data.items()}
    return {}


def save_seen_posts(seen: dict[str, dict[str, str]], path: Path = SEEN_FILE) -> None:
    """Zapisuje mapę wysłanych wpisów do seen_posts.json (atomowo)."""
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def fetch_html(session: requests.Session, url: str) -> str | None:
    """Pobiera kod HTML strony z obsługą błędów sieciowych."""
    try:
        response = session.get(url, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except requests.RequestException as exc:
        logger.error("Błąd pobierania strony %s: %s", url, exc)
        return None


def fetch_article_teaser(session: requests.Session, url: str) -> str:
    """Opcjonalnie pobiera krótką zajawkę ze strony szczegółów (gdy kafelek jej nie zawiera)."""
    html = fetch_html(session, url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")

    for selector, attr in (
        ('meta[name="description"]', "content"),
        ('meta[property="og:description"]', "content"),
    ):
        meta = soup.select_one(selector)
        if meta and meta.get(attr):
            text = clean_text(str(meta.get(attr)))
            if text:
                return truncate(text, MAX_EMBED_DESCRIPTION)

    for container_selector in (
        '[itemprop="description"]',
        "div.teaser",
        "section.mod-news_pi1 .lead",
        "section.mod-news_pi1 .teaser-text",
        "section.mod-news_pi1 .news-text-wrap p",
        "section.mod-news_pi1 .text-block-16 p",
        "main article p",
        "main p",
    ):
        for elem in soup.select(container_selector):
            text = clean_text(elem.get_text(" ", strip=True))
            if len(text) >= 40:
                return truncate(text, MAX_EMBED_DESCRIPTION)

    return ""


def parse_card(card: Tag, source: SourceConfig) -> Post | None:
    """Parsuje pojedynczy kafelek <div class='news-item'> z systemu TYPO3 UŁ."""
    link_tag = card.select_one('div[class*="item-title"] a[href]') or card.select_one("a[href]")
    if not link_tag:
        return None

    raw_href = link_tag.get("href")
    if not raw_href:
        return None

    post_url = normalize_url(str(raw_href), source.url)
    title = clean_text(link_tag.get_text(" ", strip=True)) or clean_text(str(link_tag.get("title", "")))
    if not title:
        return None

    # Opis / zajawka (obecna m.in. na kafelkach WFiIS w .text-block-16)
    desc_tag = card.select_one(".text-block-16")
    description = truncate(clean_text(desc_tag.get_text(" ", strip=True)), MAX_EMBED_DESCRIPTION) if desc_tag else ""

    # Data oraz typ / kategoria z bloków .date-categories
    date_text = ""
    meta_parts: list[str] = []
    date_blocks = card.select(".date-categories")
    if date_blocks:
        first_block = date_blocks[0]
        first_span = first_block.find("span")
        if first_span:
            date_text = clean_text(first_span.get_text(" ", strip=True))

        # Tekst po separatorze "|" (np. "Wydarzenia" lub "Aktualności")
        full_first = clean_text(first_block.get_text(" ", strip=True))
        if "|" in full_first:
            after_pipe = clean_text(full_first.split("|", 1)[1])
            if after_pipe:
                meta_parts.append(after_pipe)

        for extra_block in date_blocks[1:]:
            strong = extra_block.find("strong")
            if strong:
                cat_text = clean_text(strong.get_text(" ", strip=True))
                if cat_text and cat_text not in meta_parts:
                    meta_parts.append(cat_text)

    # Miniaturka graficzna
    thumbnail_url: str | None = None
    img_tag = card.select_one(".image img") or card.select_one("img")
    if img_tag:
        raw_src = img_tag.get("src") or img_tag.get("data-src")
        if raw_src and not str(raw_src).startswith("data:"):
            thumbnail_url = urljoin(source.url, str(raw_src).strip())

    return Post(
        post_id=post_url,
        title=truncate(title, MAX_EMBED_TITLE),
        url=post_url,
        source_name=source.name,
        source_url=source.url,
        color=source.color,
        date=date_text or "Brak daty",
        description=description,
        thumbnail_url=thumbnail_url,
        category=" · ".join(meta_parts) if meta_parts else None,
    )


def scrape_source(session: requests.Session, source: SourceConfig) -> list[Post]:
    """Pobiera i parsuje wszystkie kafelki aktualności/wydarzeń dla jednego źródła."""
    logger.info("Pobieranie źródła: %s (%s)", source.name, source.url)
    html = fetch_html(session, source.url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("section.mod-news_pi1 div.news-item")
    if not cards:
        cards = soup.select("div.news-item")

    posts: list[Post] = []
    seen_in_page: set[str] = set()
    for card in cards:
        post = parse_card(card, source)
        if not post or post.post_id in seen_in_page:
            continue
        seen_in_page.add(post.post_id)
        posts.append(post)

    logger.info("Znaleziono %d wpisów na stronie %s.", len(posts), source.name)
    return posts


def build_discord_payload(post: Post) -> dict[str, Any]:
    """Buduje estetyczny payload Discord Embed dla pojedynczego wpisu."""
    fields: list[dict[str, Any]] = [
        {"name": "Źródło", "value": post.source_name, "inline": True},
        {"name": "Data", "value": post.date, "inline": True},
    ]
    if post.category:
        fields.append({"name": "Kategoria", "value": post.category, "inline": True})

    embed: dict[str, Any] = {
        "title": post.title,
        "url": post.url,
        "color": post.color,
        "author": {
            "name": post.source_name,
            "url": post.source_url,
        },
        "fields": fields,
        "footer": {
            "text": f"UniLodz News Bot • {post.source_name}",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if post.description:
        embed["description"] = post.description
    if post.thumbnail_url:
        embed["thumbnail"] = {"url": post.thumbnail_url}

    return {
        "username": "Aktualności UŁ",
        "embeds": [embed],
    }


def send_discord_webhook(
    session: requests.Session,
    webhook_url: str,
    post: Post,
    max_retries: int = 3,
) -> bool:
    """Wysyła pojedynczy embed do Discord Webhook z obsługą rate-limit (HTTP 429)."""
    payload = build_discord_payload(post)

    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(
                webhook_url,
                json=payload,
                headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in (200, 204):
                return True

            if response.status_code == 429:
                try:
                    retry_after = float(response.json().get("retry_after", 2.0))
                except (ValueError, AttributeError):
                    retry_after = 2.0
                logger.warning(
                    "Limit zapytań Discord (429). Ponawiam za %.2f s (próba %d/%d)...",
                    retry_after,
                    attempt,
                    max_retries,
                )
                time.sleep(retry_after + 0.25)
                continue

            logger.error(
                "Discord zwrócił status %d dla '%s': %s",
                response.status_code,
                post.title,
                response.text[:300],
            )
            return False
        except requests.RequestException as exc:
            logger.error(
                "Błąd sieci przy wysyłce na Discord (próba %d/%d): %s",
                attempt,
                max_retries,
                exc,
            )
            time.sleep(2.0 * attempt)

    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pobiera aktualności z WFiIS UŁ i UŁ oraz wysyła nowe wpisy na Discord."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tylko pobiera i wypisuje nowe wpisy, bez wysyłania na Discord i bez zapisu seen_posts.json.",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Zapisuje aktualne wpisy jako już widziane bez wysyłania ich na Discord.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url and not args.dry_run and not args.init:
        logger.error(
            "Brak zmiennej środowiskowej DISCORD_WEBHOOK_URL. "
            "Ustaw ją w pliku .env, środowisku systemu lub GitHub Secrets."
        )
        return 1

    seen = load_seen_posts()
    logger.info("Załadowano %d wcześniej zapisanych wpisów z %s.", len(seen), SEEN_FILE.name)

    session = requests.Session()
    sent_count = 0
    failed_count = 0
    initialized_count = 0

    for source in SOURCES:
        posts = scrape_source(session, source)
        new_posts = [p for p in posts if p.post_id not in seen]

        if not new_posts:
            logger.info("Brak nowych wpisów dla źródła %s.", source.name)
            continue

        logger.info("Nowe wpisy dla %s: %d", source.name, len(new_posts))

        # Odwracamy kolejność, aby najnowszy wpis trafił na sam dół kanału Discord
        for post in reversed(new_posts):
            if not post.description and not args.init:
                post.description = fetch_article_teaser(session, post.url)

            if args.dry_run:
                logger.info(
                    "[DRY-RUN] [%s] %s | %s | %s | img=%s | desc=%s",
                    post.source_name,
                    post.date,
                    post.title,
                    post.url,
                    bool(post.thumbnail_url),
                    truncate(post.description, 80) if post.description else "(brak)",
                )
                continue

            if args.init:
                seen[post.post_id] = {
                    "title": post.title,
                    "source": post.source_name,
                    "date": post.date,
                    "seen_at": datetime.now(timezone.utc).isoformat(),
                }
                initialized_count += 1
                continue

            if send_discord_webhook(session, webhook_url, post):
                sent_count += 1
                seen[post.post_id] = {
                    "title": post.title,
                    "source": post.source_name,
                    "date": post.date,
                    "seen_at": datetime.now(timezone.utc).isoformat(),
                }
                save_seen_posts(seen)
                logger.info("Wysłano na Discord: [%s] %s", post.source_name, post.title)
                time.sleep(WEBHOOK_DELAY_SECONDS)
            else:
                failed_count += 1

    if args.init and initialized_count:
        save_seen_posts(seen)
        logger.info("Zainicjalizowano bazę %s (%d wpisów).", SEEN_FILE.name, initialized_count)

    logger.info(
        "Zakończono. Wysłano: %d, błędy wysyłki: %d, łącznie w bazie: %d.",
        sent_count,
        failed_count,
        len(seen),
    )
    return 1 if failed_count > 0 and sent_count == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
