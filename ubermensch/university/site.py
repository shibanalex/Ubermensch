"""Сайт вуза (mospolytech.ru): поиск, чтение страниц и документов, новости.

Сайт отдаёт готовый HTML, поэтому браузер не нужен: обычные HTTP-запросы, в десятки раз
быстрее. Документы (PDF, Word) превращаются в текст так же, как при импорте.
"""
from __future__ import annotations

import re
import ssl
import tempfile
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://mospolytech.ru"
HOSTS = ("mospolytech.ru",)  # читаем только сайты вуза (и поддомены)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
TIMEOUT = 25
# Сервер mospolytech.ru не отдаёт промежуточный сертификат. Windows докачивает его сама
# (AIA), OpenSSL: нет, поэтому он лежит рядом: системные корни + этот промежуточный.
TLS = ssl.create_default_context()
TLS.load_verify_locations(Path(__file__).with_name("certs") / "globalsign-gcc-r46-dv-tls-ca-2025.pem")
MAX_BYTES = 15_000_000
MAX_TEXT = 60_000  # столько текста страницы отдаём мозгу
SKIP_TAGS = {"script", "style", "noscript", "svg", "header", "footer", "nav", "form", "button"}
BLOCKS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
          "table", "ul", "ol", "dd", "dt"}


class _Text(HTMLParser):
    """Видимый текст страницы и ссылки; шапка, меню, подвал и скрипты пропускаются."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.links, self.title = base, [], [], ""
        self._skip, self._in_title, self._link = 0, False, None

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and (href := dict(attrs).get("href")):
            self._link = [urllib.parse.urljoin(self.base, href), ""]
        if tag in BLOCKS and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link:
            self.links.append((self._link[0], " ".join(self._link[1].split())))
            self._link = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._link is not None:
            self._link[1] += data
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()


def parse_html(html: str, url: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(заголовок, текст, ссылки [(адрес, подпись)]). Если есть <main>: берём только его."""
    main = re.search(r"<main\b.*?</main>", html, re.S | re.I)
    parser = _Text(url)
    parser.feed(main.group(0) if main else html)
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return (unescape(" ".join(title.group(1).split())) if title else "", parser.text(),
            parser.links)


def allowed(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in HOSTS)


def fetch(url: str, opener=None) -> tuple[bytes, str, str]:
    """(содержимое, тип, итоговый адрес после перенаправлений)."""
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    open_url = opener.open if opener else lambda r, timeout: urllib.request.urlopen(
        r, timeout=timeout, context=TLS)
    with open_url(request, timeout=TIMEOUT) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("файл больше 15 МБ")
        return raw, response.headers.get("Content-Type", ""), response.geturl()


def document_text(raw: bytes, url: str, kind: str) -> str | None:
    """Текст PDF или Word: тем же способом, что при импорте документов."""
    from .. import ingest
    suffix = ".pdf" if "pdf" in kind or url.lower().endswith(".pdf") else \
        ".docx" if "officedocument" in kind or url.lower().endswith(".docx") else None
    if suffix is None:
        return None
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"doc{suffix}"
        path.write_bytes(raw)
        try:
            return ingest.read_document(path)
        except Exception:  # битый или защищённый документ
            return None


def _decode(raw: bytes, kind: str) -> str:
    charset = re.search(r"charset=([\w-]+)", kind)
    return raw.decode(charset.group(1) if charset else "utf-8", errors="replace")


def read(url: str, opener=None) -> dict:
    """Страница или документ вуза текстом: {"url", "title", "text", "links"}."""
    if not allowed(url):
        raise ValueError("Это не сайт вуза — для других сайтов есть WebFetch.")
    raw, kind, final = fetch(url, opener)
    if "html" not in kind:
        text = document_text(raw, final, kind)
        if text is None:
            raise ValueError(f"Не умею читать такой файл ({kind or 'неизвестный тип'})")
        return {"url": final, "title": final.rsplit("/", 1)[-1], "text": text[:MAX_TEXT],
                "links": []}
    title, text, links = parse_html(_decode(raw, kind), final)
    return {"url": final, "title": title, "text": text[:MAX_TEXT],
            "links": [{"url": u, "text": t} for u, t in links if allowed(u) and t][:150]}


def search(query: str, limit: int = 10) -> list[dict]:
    """Поиск по сайту вуза: [{"title", "url", "section", "snippet"}]."""
    raw, kind, _ = fetch(f"{BASE}/search/?q={urllib.parse.quote(query)}")
    html = _decode(raw, kind)
    results = []
    for block in html.split('class="search-result-item"')[1:limit + 1]:
        link = re.search(r'search-result-item__link[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>',
                         block, re.S)
        if not link:
            continue
        section = re.search(r'search-result-item__path-link"[^>]*>(.*?)</a>', block, re.S)
        snippet = re.search(r'search-result-item__text[^>]*>(.*?)</div>', block, re.S)
        clean = lambda s: unescape(" ".join(re.sub(r"<[^>]+>", "", s).split()))  # noqa: E731
        url = urllib.parse.urljoin(BASE, link.group(1)).split("?sphrase_id")[0]
        results.append({"title": clean(link.group(2)), "url": url,
                        "section": clean(section.group(1)) if section else "",
                        "snippet": clean(snippet.group(1))[:400] if snippet else ""})
    return results


def news(pages: int = 2) -> list[dict]:
    """Свежие новости и объявления: [{"title", "url", "date", "text"}], новые сверху."""
    items = []
    for page in range(1, pages + 1):
        raw, kind, _ = fetch(f"{BASE}/news/" + (f"?PAGEN_1={page}" if page > 1 else ""))
        html = _decode(raw, kind)
        for block in html.split('class="card-news-wide-list__item')[1:]:
            link = re.search(r'card-news-wide__link" href="([^"]+)"', block)
            title = re.search(r'card-news-wide__title">(.*?)</div>', block, re.S)
            date = re.search(r'card-news-wide__date">(.*?)</div>', block, re.S)
            text = re.search(r'card-news-wide__text">(.*?)</div>', block, re.S)
            if not link or not title:
                continue
            clean = lambda s: unescape(" ".join(re.sub(r"<[^>]+>", " ", s).split()))  # noqa: E731
            items.append({"url": urllib.parse.urljoin(BASE, link.group(1)),
                          "title": clean(title.group(1)),
                          "date": clean(date.group(1)) if date else "",
                          "text": clean(text.group(1))[:500] if text else ""})
    return items
