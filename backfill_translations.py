"""One-time backfill: translate decisions that predate the translation feature.

Existing decisions were written in Chinese (the agent's old output language)
and have no `translations`. This marks them lang='zh' and stores an English
translation so the dashboard can offline-switch their content too.

  python backfill_translations.py                 # agent from .env
  python backfill_translations.py --all           # every data/*.db
"""
import argparse
import json
from pathlib import Path

from trader.config import DATA_DIR, load_settings
from trader.engine import create_engine
from trader.storage import Repository
from trader.translate import translate_decision


def backfill_db(db_path: Path, engine):
    repo = Repository(db_path)
    rows = repo.conn.execute(
        "SELECT id, raw_json, lang, translations FROM decisions ORDER BY id"
    ).fetchall()
    todo = [r for r in rows if not r["translations"]]
    print(f"{db_path.name}: {len(todo)}/{len(rows)} decisions need backfill")
    for r in todo:
        try:
            base = json.loads(r["raw_json"])
        except (json.JSONDecodeError, TypeError):
            print(f"  decision {r['id']}: unparseable raw_json, skipped")
            continue
        # These pre-date the language switch → agent wrote Chinese; translate → English
        native = r["lang"] or "zh"
        target = "en" if native == "zh" else "zh"
        try:
            translated = translate_decision(engine, base, target)
            repo.conn.execute(
                "UPDATE decisions SET lang = ?, translations = ? WHERE id = ?",
                (native, json.dumps({target: translated}, ensure_ascii=False), r["id"]),
            )
            repo.conn.commit()
            print(f"  decision {r['id']}: {native} → +{target} ✓")
        except Exception as e:
            print(f"  decision {r['id']}: translation failed ({e})")
    repo.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--all", action="store_true", help="process every data/*.db")
    args = ap.parse_args()

    settings = load_settings(model=args.model)
    engine = create_engine(settings)
    dbs = sorted(DATA_DIR.glob("*.db")) if args.all else [settings.db_path]
    for db in dbs:
        backfill_db(db, engine)


if __name__ == "__main__":
    main()
