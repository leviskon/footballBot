"""``python -m bot --preview``: one collection pass printed to the console, no Telegram.

Useful to see what the filters let through after editing the catalogue or the sources.
"""

from __future__ import annotations

from collections import Counter

from .classifier import Classifier
from .config import Config
from .catalog import CATEGORY_BY_KEY
from .net import HttpClient
from .news import NewsCollector
from .settings import SettingsStore
from .sources import LANGUAGES, SOURCE_BY_KEY
from .storage import NewsStatus, Storage


async def preview(config: Config, *, show_filtered: int = 25) -> int:
    db = await Storage.open(":memory:")
    http = HttpClient(config.request_timeout)
    await http.start()
    try:
        settings = SettingsStore(db)
        await settings.update(lambda s: s.langs.update(LANGUAGES))
        collector = NewsCollector(http, db, settings, Classifier(),
                                  max_age_hours=config.max_news_age_hours,
                                  interval=config.news_interval)
        report = await collector.collect(seeding=False)
        records = sorted(report.records, key=lambda r: r.published_at)
        titles = {r.id: r.title for r in records}

        def label(record) -> str:
            cats = ",".join(CATEGORY_BY_KEY[k].short for k in record.categories
                            if k in CATEGORY_BY_KEY) or "—"
            source = SOURCE_BY_KEY[record.source].name
            return f"[{cats}] {source}: {record.title}"

        print(f"\nИсточников ответило: {report.sources_ok}, с ошибкой: {report.sources_failed}")
        for source in collector.sources:
            state = collector.health[source.key]
            if state.failures:
                print(f"  ✗ {source.name}: {state.last_error}")
        counts = Counter(r.status for r in records)
        print(f"Материалов: {len(records)} — " + ", ".join(f"{k}: {v}" for k, v in counts.items()))

        print(f"\n=== Будет отправлено (свежее {config.max_news_age_hours} ч) ===")
        for record in records:
            if record.status == NewsStatus.PENDING:
                print("  " + label(record))

        print("\n=== Отсеяно как дубли ===")
        for record in records:
            if record.status == NewsStatus.DUPLICATE:
                print(f"  {label(record)}\n      ↳ дубль: {titles.get(record.dup_of or '', '?')}")

        filtered = [r for r in records if r.status == NewsStatus.FILTERED]
        print(f"\n=== Не по теме (первые {show_filtered} из {len(filtered)}) ===")
        for record in filtered[-show_filtered:]:
            print("  " + label(record))
        return 0
    finally:
        await http.close()
        await db.close()
