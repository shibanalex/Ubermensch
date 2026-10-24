"""Synthetic retrieval regression benchmark; no network or personal database."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ubermensch.store import Store


def evaluate():
    fixture = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/memory_eval.json').read_text())
    store = Store(':memory:')
    try:
        ids = [store.add_fact('learning', text, 1, 'synthetic') for text in fixture['facts']]
        for n in range(200):
            store.add_fact('profile', f'Другой важный факт номер {n}', 5, 'synthetic')
        retrieval_hits = baseline_hits = 0
        for case in fixture['queries']:
            expected = ids[case['fact']]
            retrieval_hits += expected in [r['id'] for r in store.relevant_facts(case['query'], 5)]
            baseline_hits += expected in [r['id'] for r in store.facts(5)]
        return {'cases': len(fixture['queries']), 'hit_at_5': retrieval_hits / len(fixture['queries']),
                'importance_only_hit_at_5': baseline_hits / len(fixture['queries']),
                'scope': 'synthetic lexical retrieval; not an LLM answer-quality evaluation'}
    finally:
        store.db.close()


if __name__ == '__main__':
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['hit_at_5'] == 1 else 1)
