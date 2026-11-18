"""Copy every table of the bot's PostgreSQL database into another PostgreSQL database.

Usage (URLs come from the environment so they never land in shell history or logs):

    SOURCE_DATABASE_URL=postgresql://...old... TARGET_DATABASE_URL=postgresql://...new... \
        python scripts/copy_database.py [--replace]

The target schema is created the same way the bot creates it at startup. Without --replace
the script refuses to touch a target that already has rows; with it, the target tables are
emptied first, which lets you re-run the copy right before switching DATABASE_URL.
Tables are copied parents first (foreign keys stay valid without superuser rights),
SERIAL sequences are moved past the copied ids, and row counts are compared at the end.
"""
import csv
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from queue_bot.storage.database import connect  # noqa: E402  (creates the schema on the target)


def tables(conn) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1")]


def parents_first(conn, names: list[str]) -> list[str]:
    edges = {name: set() for name in names}
    for child, parent in conn.execute(
            "SELECT c.conrelid::regclass::text, c.confrelid::regclass::text FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.contype = 'f' AND n.nspname = 'public'"):
        child, parent = child.strip('"'), parent.strip('"')
        if child in edges and parent in edges and child != parent:
            edges[child].add(parent)
    ordered, done = [], set()

    def visit(name, trail=()):
        if name in done:
            return
        if name in trail:
            raise SystemExit(f'Foreign keys form a cycle through {name}; copy cannot order it.')
        for parent in sorted(edges[name]):
            visit(parent, trail + (name,))
        done.add(name)
        ordered.append(name)

    for name in names:
        visit(name)
    return ordered


def columns(conn, table: str) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position", (table,))]


def count(conn, table: str) -> int:
    return conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]


class PostgresSource:
    def __init__(self, url: str):
        self.conn = psycopg.connect(url, connect_timeout=20)

    def tables(self) -> list[str]:
        return tables(self.conn)

    def columns(self, table: str) -> list[str]:
        return columns(self.conn, table)

    def rows(self, table: str) -> int:
        return count(self.conn, table)

    def copy_into(self, target, table: str, cols: list[str]) -> None:
        names = sql.SQL(', ').join(sql.Identifier(c) for c in cols)
        out = sql.SQL('COPY {} ({}) TO STDOUT').format(sql.Identifier(table), names)
        into = sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(table), names)
        with self.conn.cursor().copy(out) as reader, target.cursor().copy(into) as writer:
            for chunk in reader:
                writer.write(chunk)

    def close(self) -> None:
        self.conn.close()


class CsvSource:
    """A folder of `COPY ... TO STDOUT WITH (FORMAT csv, HEADER)` files plus manifest.json."""

    def __init__(self, folder: str):
        self.folder = Path(folder)
        self.manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))

    def tables(self) -> list[str]:
        return sorted(self.manifest)

    def columns(self, table: str) -> list[str]:
        with open(self.folder / f'{table}.csv', encoding='utf-8', newline='') as handle:
            return next(csv.reader(handle))

    def rows(self, table: str) -> int:
        return int(self.manifest[table]['rows'])

    def copy_into(self, target, table: str, cols: list[str]) -> None:
        names = sql.SQL(', ').join(sql.Identifier(c) for c in self.columns(table))
        into = sql.SQL('COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)').format(
            sql.Identifier(table), names)
        with open(self.folder / f'{table}.csv', 'rb') as handle, target.cursor().copy(into) as writer:
            while chunk := handle.read(1 << 16):
                writer.write(chunk)

    def close(self) -> None:
        pass


def main() -> None:
    source_url, target_url = os.getenv('SOURCE_DATABASE_URL', ''), os.getenv('TARGET_DATABASE_URL', '')
    csv_dir = os.getenv('SOURCE_CSV_DIR', '')
    if not (source_url or csv_dir) or not target_url:
        raise SystemExit('Set TARGET_DATABASE_URL and either SOURCE_DATABASE_URL or SOURCE_CSV_DIR.')
    if source_url == target_url:
        raise SystemExit('Source and target are the same database.')
    replace = '--replace' in sys.argv[1:]

    connect(target_url).close()  # the bot's own schema, including additive migrations
    clean = lambda url: url.replace('&channel_binding=require', '')
    source = CsvSource(csv_dir) if csv_dir else PostgresSource(clean(source_url))
    try:
        with psycopg.connect(clean(target_url), connect_timeout=20) as target:
            copy_all(source, target, replace)
    finally:
        source.close()


def copy_all(source, target, replace: bool) -> None:
    source_tables, target_tables = source.tables(), set(tables(target))
    missing = [t for t in source_tables if t not in target_tables]
    if missing:
        raise SystemExit(f'Target has no table(s) {missing}: is the target on an older bot version?')
    order = parents_first(target, source_tables)
    busy = [t for t in order if count(target, t)]
    if busy and not replace:
        raise SystemExit(f'Target already has rows in {busy}. Re-run with --replace to overwrite them.')

    with target.transaction():
        if busy:
            target.execute(sql.SQL('TRUNCATE {} CASCADE').format(
                sql.SQL(', ').join(sql.Identifier(t) for t in order)))
        for table in order:
            target_cols = set(columns(target, table))
            source_cols = source.columns(table)
            extra = [c for c in source_cols if c not in target_cols]
            if extra and isinstance(source, CsvSource):
                raise SystemExit(f'{table}.csv has column(s) {extra} the target lacks.')
            source.copy_into(target, table, [c for c in source_cols if c in target_cols])
        for table, column, sequence in target.execute(
                "SELECT c.table_name, c.column_name, pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) "
                "FROM information_schema.columns c WHERE c.table_schema = 'public' "
                "AND pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) IS NOT NULL").fetchall():
            target.execute(sql.SQL('SELECT setval(%s, GREATEST(COALESCE(MAX({}), 0), 1), MAX({}) IS NOT NULL) FROM {}').format(
                sql.Identifier(column), sql.Identifier(column), sql.Identifier(table)), (sequence,))

    report, wrong = [], []
    for table in order:
        before, after = source.rows(table), count(target, table)
        report.append(f'{table}: {after}' + ('' if before == after else f' (source has {before})'))
        if before != after:
            wrong.append(table)
    print('\n'.join(report))
    if wrong:
        raise SystemExit(f'Row counts differ for {wrong}: the source changed during the copy or rows were rejected.')
    print(f'OK: {len(order)} tables copied.')


if __name__ == '__main__':
    main()
