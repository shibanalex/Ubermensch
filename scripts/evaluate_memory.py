"""Synthetic retrieval regression benchmark; no network or personal database.

memory_eval.json — запрос и факт с общими словами; memory_eval_forms.json — разные формы слов,
как в живой речи («общежитием» / «в общежитии»). Порог по каждому набору — не ниже достигнутого.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ubermensch.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures'
THRESHOLDS = {'memory_eval.json': 1.0, 'memory_eval_forms.json': 0.9}


def evaluate_fixture(name):
    fixture = json.loads((FIXTURES / name).read_text())
    store = Store(':memory:')
    try:
        ids = [store.add_fact('learning', text, 1, 'synthetic') for text in fixture['facts']]
        for text in fixture.get('near_misses', []):
            store.add_fact('profile', text, 4, 'synthetic')
        for n in range(200):
            store.add_fact('profile', f'Другой важный факт номер {n}', 5, 'synthetic')
        retrieval_hits = baseline_hits = 0
        misses = []
        for case in fixture['queries']:
            expected = ids[case['fact']]
            hit = expected in [r['id'] for r in store.relevant_facts(case['query'], 5)]
            retrieval_hits += hit
            if not hit:
                misses.append(case['query'])
            baseline_hits += expected in [r['id'] for r in store.facts(5)]
        cases = len(fixture['queries'])
        return {'cases': cases, 'hit_at_5': round(retrieval_hits / cases, 3),
                'importance_only_hit_at_5': round(baseline_hits / cases, 3),
                'threshold': THRESHOLDS[name], 'misses': misses}
    finally:
        store.db.close()


def evaluate():
    return {'fixtures': {name: evaluate_fixture(name) for name in THRESHOLDS},
            'scope': 'synthetic stem-based retrieval; not an LLM answer-quality evaluation'}


if __name__ == '__main__':
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if all(r['hit_at_5'] >= r['threshold'] for r in result['fixtures'].values()) else 1)
