"""Search the JLCPCB/LCSC parts cache that konnect downloads (~/.konnect/jlcpcb.db, ~800k parts).

konnect's own search_jlcpcb_parts is a plain keyword match; SQL on the cache is far more useful. Refresh the cache
with konnect's download_jlcpcb_database tool (source: bouni/kicad-jlcpcb-tools). For part research with a BOM
behind it use bomi; this is the quick lookup.

  pcbkit jlc --lcsc C2980109 C15127              # look up parts by LCSC number
  pcbkit jlc --desc "%orange%" --package "0402%" --category "%LED%"
  pcbkit jlc --mpn "74HC138%" --min-stock 5000
  pcbkit jlc --sql "select LCSC,MFR_Part,Stock from components where ..."

Table: components(LCSC, MFR_Part, Package, Manufacturer, Library_Type, Description, Datasheet, Price, Stock, Category)
Library_Type: Basic / Preferred (no JLC extended-part fee) or Extended. Results sort Basic/Preferred first, then stock.
"""
import argparse
import os
import sqlite3

DB = os.path.expanduser("~/.konnect/jlcpcb.db")


def add_args(ap):
    ap.add_argument("--lcsc", nargs="*")
    ap.add_argument("--mpn")
    ap.add_argument("--desc", action="append")
    ap.add_argument("--package")
    ap.add_argument("--category")
    ap.add_argument("--min-stock", type=int, default=1)
    ap.add_argument("--basic", action="store_true", help="Basic/Preferred only")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--sql")


def search(a):
    if not os.path.exists(DB):
        raise SystemExit(f"no parts cache at {DB}: run konnect's download_jlcpcb_database first")
    db = sqlite3.connect(DB)
    if a.sql:
        return db.execute(a.sql).fetchall()
    where, args = ["Stock >= ?"], [a.min_stock]
    if a.lcsc:
        where, args = [f"LCSC in ({','.join('?' * len(a.lcsc))})"], list(a.lcsc)
    if a.mpn:
        where.append("MFR_Part like ?"); args.append(a.mpn)
    for d in a.desc or []:
        where.append("Description like ?"); args.append(d)
    if a.package:
        where.append("Package like ?"); args.append(a.package)
    if a.category:
        where.append("Category like ?"); args.append(a.category)
    if a.basic:
        where.append("Library_Type in ('Basic','Preferred')")
    return db.execute(f"""select LCSC, MFR_Part, Manufacturer, Package, Library_Type, Stock, Price, substr(Description,1,70)
                          from components where {' and '.join(where)}
                          order by (Library_Type in ('Basic','Preferred')) desc, Stock desc limit ?""",
                      args + [a.limit]).fetchall()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_args(ap)
    a = ap.parse_args(argv)
    for r in search(a):
        print(" | ".join(str(x) for x in r))


if __name__ == "__main__":
    main()
