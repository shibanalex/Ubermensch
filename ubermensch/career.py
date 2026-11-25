"""Read-only job discovery. Sources and their restrictions travel with every vacancy."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os

import aiohttp
import re

from .planning import required

REMOTIVE_URL = 'https://remotive.com/api/remote-jobs'
ARBEITNOW_URL = 'https://www.arbeitnow.com/api/job-board-api'  # Германия и ЕС, бесплатное публичное API
ARBEITNOW_PAGES = 5  # 300 вакансий на страницу; снимок раз в 6 часов
# Визовая поддержка и релокация: по формулировкам в тексте: поля в выдаче нет
VISA = re.compile(r'(?i)visa[\s-]*(sponsor|support|assistance)|sponsor(ship)?\s+(of\s+)?(your\s+|a\s+)?(work\s+)?visa'
                  r'|relocation\s+(package|support|assistance|bonus|budget)|blue\s*card|we\s+(help|support)\s+with\s+relocation'
                  r'|visumsunterstützung|umzugsunterstützung')
HH_URL = 'https://api.hh.ru'
_remotive_lock = asyncio.Lock()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag in {'p', 'br', 'li', 'div'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(str(value or '')[:100000])
    return ''.join(parser.parts).strip()[:20000]


async def request_json(url, params=None):
    # URLs are constants or validated numeric hh IDs, never arbitrary model-provided URLs.
    agent = os.getenv('HH_USER_AGENT', 'UbermenschPersonalAssistant/0.1')
    headers = {'User-Agent': agent, 'Accept': 'application/json'}
    if url.startswith(HH_URL + '/'):
        headers['HH-User-Agent'] = agent
        if token := os.getenv('HH_ACCESS_TOKEN', '').strip():
            headers['Authorization'] = 'Bearer ' + token
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40), trust_env=True) as session:
            async with session.get(url, params=params, headers=headers, allow_redirects=False) as response:
                if response.status != 200:
                    if response.status in {401, 403} and url.startswith(HH_URL + '/'):
                        raise ValueError(f'hh отказал в доступе (HTTP {response.status}). Проверь '
                                         'HH_USER_AGENT, HH_ACCESS_TOKEN и доступ приложения к API. '
                                         'Можно использовать Remotive или добавить вакансию по ссылке.')
                    raise ValueError(f'Источник вакансий вернул HTTP {response.status}; выдача не обновлена')
                parts, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 20_000_000:
                        raise ValueError('Ответ источника вакансий слишком большой')
                    parts.append(chunk)
                data = json.loads(b''.join(parts))
                if not isinstance(data, dict):
                    raise ValueError('Некорректный ответ источника вакансий')
                return data
    except (aiohttp.ClientError, TimeoutError):
        raise ValueError('Источник вакансий временно недоступен; результаты не обновлены') from None


def hh_item(raw):
    identifier = str(raw['id'])
    if not identifier.isdigit():
        raise ValueError('Некорректный ID hh')
    snippet = raw.get('snippet') or {}
    description = raw.get('description') or '\n'.join(str(snippet.get(k) or '') for k in ('requirement', 'responsibility'))
    return dict(source='hh', external_id=identifier, title=raw['name'],
        company=(raw.get('employer') or {}).get('name') or 'Компания не указана',
        url=f'https://hh.ru/vacancy/{identifier}', details={
            'description': plain(description), 'full_description': 'description' in raw,
            'location': (raw.get('area') or {}).get('name'), 'salary': raw.get('salary'),
            'experience': raw.get('experience'), 'schedule': raw.get('schedule'),
            'employment': raw.get('employment'), 'skills': raw.get('key_skills', []),
            'published_at': raw.get('published_at'), 'archived': raw.get('archived', False),
            'source_notice': 'Источник: hh.ru. Валюта, период оплаты и ограничения требуют проверки.'})


def remotive_item(raw):
    return dict(source='remotive', external_id=str(raw['id']), title=raw['title'],
        company=raw['company_name'], url=raw['url'], details={
            'description': plain(raw.get('description')), 'full_description': True,
            'location': raw.get('candidate_required_location') or 'Не указано',
            'salary_text': raw.get('salary') or 'Не указано', 'job_type': raw.get('job_type'),
            'published_at': raw.get('publication_date'),
            'source_notice': 'Источник: Remotive. Публичная выдача отстаёт на 24 часа. '
                             'Сохраняй ссылку Remotive. Remote не означает доступность из любой страны.'})


def visa_note(text):
    """«найдено в тексте» или «не сказано»: не «нет»: отсутствие фразы не значит отказ."""
    return 'найдено в тексте' if VISA.search(text or '') else 'не сказано'


def arbeitnow_item(raw):
    description = plain(raw.get('description'))
    return dict(source='arbeitnow', external_id=str(raw['slug']), title=raw['title'],
        company=raw['company_name'], url=raw['url'], details={
            'description': description, 'full_description': True,
            'location': raw.get('location') or 'Не указано', 'remote': bool(raw.get('remote')),
            'tags': raw.get('tags') or [], 'job_types': raw.get('job_types') or [],
            'visa_or_relocation': visa_note(description),
            'published_at': raw.get('created_at'),
            'source_notice': 'Источник: Arbeitnow (Германия/ЕС). Визовая поддержка — только по тексту '
                             'вакансии; язык работы и разрешение на работу проверяй отдельно.'})


async def _snapshot(store, key, fetch):
    """Общий снимок источника на все запросы: не чаще раза в 6 часов, и после перезапуска."""
    cache = store.planning_setting(key)
    now = datetime.now(timezone.utc)
    if not cache or now - datetime.fromisoformat(cache['at']) >= timedelta(hours=6):
        cache = {'at': now.isoformat(timespec='seconds'), 'items': await fetch()}
        store.set_planning_setting(key, cache)
    return cache


async def _arbeitnow():
    items = []
    for number in range(1, ARBEITNOW_PAGES + 1):
        data = await request_json(ARBEITNOW_URL, {'page': number})
        items += [arbeitnow_item(r) for r in data.get('data', [])]
        if not (data.get('links') or {}).get('next'):
            break
    return items


async def search(store, source, query, *, limit=10, page=0, area='', visa_only=False):
    query = required(query, 'Поисковый запрос', 300)
    if not 1 <= limit <= 30 or not 0 <= page <= 20:
        raise ValueError('limit: 1–30, page: 0–20')
    if source == 'hh':
        params = {'text': query, 'per_page': limit, 'page': page}
        if area:
            if not area.isdigit():
                raise ValueError('area — числовой ID региона hh или пустая строка')
            params['area'] = area
        data = await request_json(HH_URL + '/vacancies', params)
        items = [hh_item(r) for r in data['items']]
        total, pages = data.get('found'), data.get('pages')
        fetched_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    elif source in ('remotive', 'arbeitnow'):
        if area:
            raise ValueError('area поддерживается только hh; здесь проверяйте location каждой вакансии')
        async with _remotive_lock:
            if source == 'remotive':
                async def fetch():
                    return [remotive_item(r) for r in (await request_json(REMOTIVE_URL))['jobs']]
                cache = await _snapshot(store, 'remotive_cache', fetch)
            else:
                cache = await _snapshot(store, 'arbeitnow_cache', _arbeitnow)
        words = query.casefold().split()
        found = [r for r in cache['items'] if all(w in (' '.join((r['title'], r['company'],
                 ' '.join(r['details'].get('tags', [])), r['details']['description']))).casefold()
                 for w in words)]
        if visa_only:
            found = [r for r in found if visa_note(r['details']['description']) == 'найдено в тексте']
        total, pages = len(found), (len(found) + limit - 1) // limit
        items = found[page * limit:(page + 1) * limit]
        fetched_at = cache['at']
    else:
        raise ValueError('Источник должен быть hh, remotive или arbeitnow')
    items = [{**item, 'details': {**item['details'], 'source_fetched_at': fetched_at}} for item in items]
    with store.transaction():
        saved = [store.save_opportunity(**item) for item in items]
    # Keep tool output small enough for a whole page; fetch descriptions with career_get.
    return {'source': source, 'fetched_at': fetched_at, 'found': total, 'pages': pages,
            'items': [{k: row[k] for k in ('id', 'source', 'title', 'company', 'url')} |
                     {'location': row['details'].get('location'),
                      'salary': row['details'].get('salary', row['details'].get('salary_text')),
                      'visa_or_relocation': visa_note(row['details'].get('description', ''))}
                     for row in saved],
            'note': {'remotive': 'Remotive: задержка публикации 24 часа; указывай источник и ссылку.',
                     'arbeitnow': 'Arbeitnow: Германия и ЕС; визовая поддержка — по тексту вакансии, '
                                  '«не сказано» не значит «нет».'}.get(source, 'Источник: hh.ru; детали через career_get.')}


async def get_opportunity(store, opportunity_id):
    item = store.opportunity(opportunity_id)
    if item['source'] == 'hh':
        identifier = item['external_id']
        if not identifier.isdigit():
            raise ValueError('Некорректный ID hh')
        raw = await request_json(HH_URL + '/vacancies/' + identifier)
        normalized = hh_item(raw)
        normalized['details']['source_fetched_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return store.save_opportunity(**normalized)
    return item
