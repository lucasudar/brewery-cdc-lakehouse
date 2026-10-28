#!/usr/bin/env python3
"""Generate docs/diagrams/architecture.drawio (one page per view) + pages.tsv for render_png.py.

Hand-placed layout (the graph is small); `make diagrams` renders the PNGs and checks the geometry.
Icons are committed in icons/; tools without an icon get a lettered tile.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
OUT = HERE / "architecture.drawio"
PAGES = HERE / "pages.tsv"

# ---------------------------------------------------------------- icons


def fallback_svg(text: str, color: str) -> str:
    size = 26 if len(text) <= 2 else 20
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
        f'fill="{color}"/><text x="32" y="{32 + size * 0.36:.0f}" font-family="Arial" font-size="{size}" '
        f'font-weight="bold" fill="#fff" text-anchor="middle">{html.escape(text)}</text></svg>'
    )


def icon(name: str | None, fb: tuple[str, str] = ("?", "#888888")) -> str:
    path = ICONS / f"{name}.svg" if name else None
    data = path.read_text(encoding="utf-8") if path and path.exists() else fallback_svg(*fb)
    return "data:image/svg+xml," + base64.b64encode(data.encode()).decode()


POSTGRES = icon("postgresql")
DEBEZIUM = icon(None, ("DBZ", "#5BA02F"))
REDPANDA = icon(None, ("RP", "#E2401B"))
PYTHON = icon("python")
ICEBERG = icon("iceberg")
SEAWEED = icon("seaweedfs")
DBT = icon("dbt")
DUCKDB = icon("duckdb")
FASTAPI = icon("fastapi")
SWAGGER = icon("swagger")

# ---------------------------------------------------------------- drawio model


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


class Page:
    def __init__(self, name: str):
        self.name, self.cells, self.n = name, [], 0

    def _id(self) -> str:
        self.n += 1
        return f"c{self.n}"

    def add(self, value, style, x=0, y=0, w=100, h=40, parent="1"):
        cid = self._id()
        self.cells.append(("v", cid, value, style, x, y, w, h, parent))
        return cid

    def edge(self, src, tgt, label="", style="", parent="1", points=None, label_pos=None):
        cid = self._id()
        self.cells.append(("e", cid, label, style, src, tgt, parent, points, label_pos))
        return cid

    def box(self, label, x, y, w, h, fill, stroke, parent="1", fs=14):
        return self.add(
            f"<b>{label}</b>",
            f"rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
            f"verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;fontSize={fs};container=1;"
            f"collapsible=0;recursiveResize=0;dashed=0;strokeWidth=2;",
            x, y, w, h, parent,
        )

    def card(self, label, img, x, y, w=230, h=74, parent="1", fill="#ffffff", stroke="#999999", isz=36):
        return self.add(
            label,
            f"label;whiteSpace=wrap;html=1;rounded=1;arcSize=10;image={img};imageWidth={isz};"
            f"imageHeight={isz};imageAlign=left;imageVerticalAlign=middle;align=left;verticalAlign=middle;"
            f"spacingLeft={isz + 20};spacing=6;fillColor={fill};strokeColor={stroke};fontSize=11;shadow=1;",
            x, y, w, h, parent,
        )

    def step(self, label, x, y, w, h, parent="1", fill="#ffffff", stroke="#999999"):
        return self.add(
            label,
            f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=11;"
            "align=center;verticalAlign=middle;spacing=6;shadow=1;",
            x, y, w, h, parent,
        )

    def text(self, label, x, y, w, h, parent="1", extra=""):
        return self.add(
            label, f"text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;fontSize=12;{extra}", x, y, w, h, parent
        )

    def title(self, t, sub=""):
        self.text(f"<font style='font-size:26px'><b>{t}</b></font><br><font color='#666666'>{sub}</font>",
                  20, 10, 1500, 70)

    def xml(self, pid: str) -> str:
        out = [
            f'<diagram id="{pid}" name="{esc(self.name)}"><mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" '
            'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" pageWidth="1600" '
            'pageHeight="1200" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        ]
        for c in self.cells:
            if c[0] == "v":
                _, cid, v, st, x, y, w, h, par = c
                out.append(
                    f'<mxCell id="{cid}" value="{esc(v)}" style="{esc(st)}" vertex="1" parent="{par}">'
                    f'<mxGeometry x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" as="geometry"/></mxCell>'
                )
            else:
                _, cid, v, st, s, t, par, pts, lp = c
                base = (
                    "html=1;rounded=1;endArrow=block;endFill=1;fontSize=10;labelBackgroundColor=#ffffff;"
                    "strokeWidth=1.5;edgeStyle=orthogonalEdgeStyle;"
                )
                g = '<mxGeometry relative="1"' + (f' x="{lp:g}"' if lp is not None else "") + ' as="geometry">'
                if pts:
                    g += '<Array as="points">' + "".join(f'<mxPoint x="{a:g}" y="{b:g}"/>' for a, b in pts) + "</Array>"
                g += "</mxGeometry>"
                out.append(
                    f'<mxCell id="{cid}" value="{esc(v)}" style="{esc(base + st)}" edge="1" parent="{par}" '
                    f'source="{s}" target="{t}">{g}</mxCell>'
                )
        out.append("</root></mxGraphModel></diagram>")
        return "".join(out)


GREY = "#666666"


def sub(text: str) -> str:
    return f"<font color='{GREY}'>{text}</font>"


# ---------------------------------------------------------------- page 1: architecture


def architecture() -> Page:
    p = Page("1. Architecture & data flow")
    p.title(
        "Brewery lakehouse: near-real-time CDC",
        "Postgres → Debezium → Redpanda → Iceberg (bronze / silver / gold) → FastAPI · "
        "measured end to end ≈ 7 s (target 60 s) · everything in docker compose",
    )
    top, height = 100, 600

    src = p.box("Source", 20, top, 240, height, "#EEF4FB", "#6C8EBF")
    pg = p.card(f"<b>Postgres 16</b><br>{sub('beer_factory<br>novisto_consumption<br>wal_level=logical<br>REPLICA IDENTITY FULL')}",
                POSTGRES, 15, 60, 210, 110, src)

    cap = p.box("Change capture & log", 300, top, 290, height, "#FFF4E6", "#D79B00")
    dbz = p.card(f"<b>Debezium 3.3</b> (Kafka Connect)<br>{sub('pgoutput · initial snapshot<br>then streaming · heartbeat')}",
                 DEBEZIUM, 15, 60, 260, 110, cap)
    rp = p.card(f"<b>Redpanda</b> (Kafka API)<br>{sub('cdc.public.beer_factory<br>cdc.public.novisto_consumption<br>cdc.dlq · Connect offsets')}",
                REDPANDA, 15, 260, 260, 120, cap)

    ing_box = p.box("Ingestion", 630, top, 260, 280, "#EAF7EA", "#82B366")
    ing = p.card(f"<b>ingestor</b> (PyIceberg)<br>{sub('micro-batch ≤ 5 s<br>dedup per PK · LSN guard<br>schema evolution · DLQ<br>offsets in snapshot summary')}",
                 PYTHON, 15, 60, 230, 140, ing_box)

    tr_box = p.box("Transformation", 630, top + 320, 260, 280, "#F3EEFB", "#9673A6")
    tr = p.card(f"<b>transformer</b> (dbt-duckdb)<br>{sub('runs on new silver snapshots<br>pinned AT (VERSION =&gt; id)<br>23 data tests · then publish')}",
                DBT, 15, 60, 230, 130, tr_box)
    p.card(f"<b>DuckDB</b><br>{sub('in-memory engine for dbt')}", DUCKDB, 15, 205, 230, 60, tr_box, isz=28)

    lake = p.box("Lakehouse · Apache Iceberg v2", 930, top, 450, height, "#E8F4F8", "#10739E")
    bronze = p.card(f"<b>bronze.cdc_events</b><br>{sub('append-only changelog · partitioned by day')}",
                    ICEBERG, 15, 60, 420, 64, lake, fill="#FBF3E8", stroke="#B07A3A", isz=30)
    s_beer = p.card(f"<b>silver.beer_factory</b><br>{sub('current state')}", ICEBERG, 15, 150, 205, 64, lake,
                    fill="#F4F6F8", stroke="#7D8C99", isz=30)
    s_cons = p.card(f"<b>silver.novisto_<br>consumption</b><br>{sub('current state')}", ICEBERG, 230, 150, 205, 64,
                    lake, fill="#F4F6F8", stroke="#7D8C99", isz=30)
    gold = p.card(f"<b>gold.daily_style_consumption</b><br>{sub('litres per day × style × site · lineage in snapshot')}",
                  ICEBERG, 15, 330, 420, 64, lake, fill="#FFF8DC", stroke="#C9A227", isz=30)
    p.card(f"<b>Iceberg REST catalog</b><br>{sub('JDBC catalog on its<br>own Postgres')}", ICEBERG, 15, 440, 205, 70,
           lake, isz=30)
    p.card(f"<b>SeaweedFS</b><br>{sub('S3 · s3://warehouse<br>metadata + Parquet')}", SEAWEED, 230, 440, 205, 70,
           lake, isz=30)
    p.text(sub("format v2 · copy-on-write deletes · snapshot expiry + compaction"), 15, 530, 420, 40, lake)

    srv = p.box("Serving", 1480, top, 270, height, "#FBEFEF", "#B85450")
    api = p.card(f"<b>FastAPI</b><br>{sub('/v1/beers · /v1/consumption<br>/v1/metrics/daily-style-consumption<br>/freshness · /health')}",
                 FASTAPI, 15, 60, 240, 120, srv)
    docs = p.card(f"<b>OpenAPI</b><br>{sub('/docs (Swagger) · /redoc<br>examples on every model')}", SWAGGER,
                  15, 260, 240, 80, srv)

    p.edge(pg, dbz, "WAL<br>pgoutput")
    p.edge(dbz, rp, "change events<br>JSON + schema")
    p.edge(rp, ing, "consume", points=[(610, 420), (610, 230)])
    p.edge(ing, bronze, "append", points=[(915, 230), (915, 192)], label_pos=0.6)
    p.edge(ing, s_beer, "upsert /<br>delete", points=[(915, 230), (915, 270)], label_pos=0.6)
    p.edge(s_beer, tr, "read pinned<br>snapshots", "exitX=0;exitY=0.85;entryX=1;entryY=0.3;",
           points=[(900, 304), (900, 519)], label_pos=-0.45)
    p.edge(tr, gold, "publish if<br>tests pass", "exitX=1;exitY=0.8;entryX=0;entryY=0.5;",
           points=[(918, 584), (918, 462)], label_pos=-0.2)
    p.edge(lake, api, "snapshot<br>reads", "exitX=1;exitY=0.2;entryX=0;entryY=0.5;")
    p.edge(api, docs, "auto-generated")
    return p


# ---------------------------------------------------------------- page 2: semantics


def semantics() -> Page:
    p = Page("2. Exactly-once effect & recovery")
    p.title(
        "How a change is applied exactly once",
        "No two-phase commit: Iceberg snapshots carry the progress (Kafka offsets, source snapshot ids), "
        "so data and progress are committed atomically and a restart resumes from the table itself",
    )
    w, h, gap = 190, 96, 34

    ing = p.box("ingestor: one micro-batch (≤ 5 s or 5 000 events)", 20, 100, 7 * w + 6 * gap + 40, 300,
                "#EAF7EA", "#82B366")
    labels = [
        "<b>1. Poll</b><br>Redpanda, manual offsets",
        "<b>2. Parse envelope</b><br>types from Connect schema<br>bad message → <i>cdc.dlq</i>",
        "<b>3. Skip replays</b><br>offset ≤ offset already<br>committed by this table",
        "<b>4. Latest per PK</b><br>max (lsn, offset) wins<br>insert+delete → nothing",
        "<b>5. LSN guard</b><br>never overwrite a row<br>with an older change",
        "<b>6. One Iceberg commit<br>per table</b><br>delete + append (CoW)<br>+ offsets in snapshot",
        "<b>7. Kafka commit</b><br>monitoring only<br>(lag dashboards)",
    ]
    cells = [p.step(t, 20 + i * (w + gap), 50, w, h, ing,
                    fill="#FFF8DC" if i == 5 else "#ffffff", stroke="#C9A227" if i == 5 else "#999999")
             for i, t in enumerate(labels)]
    for a, b in zip(cells, cells[1:], strict=False):
        p.edge(a, b)
    restart = p.step(
        "<b>Restart / crash / rebalance</b><br>read offsets from the latest snapshot summary of every table → "
        "seek to the slowest one → each table skips what it already has. Replaying the whole topic is a no-op.",
        20 + 2 * (w + gap), 190, 3 * w + 2 * gap, 86, ing, fill="#F4F6F8", stroke="#7D8C99")
    p.edge(restart, cells[0], "resume", "exitX=0;exitY=0.5;entryX=0.5;entryY=1;")
    p.edge(cells[5], restart, "durable state", "exitX=0.5;exitY=1;entryX=1;entryY=0.5;dashed=1;")

    tr = p.box("transformer: write-audit-publish", 20, 440, 5 * w + 4 * gap + 300, 260, "#F3EEFB", "#9673A6")
    t_labels = [
        "<b>1. Resolve input</b><br>current snapshot id of<br>each silver table",
        "<b>2. Already published?</b><br>compare with lineage in<br>gold snapshot summary",
        "<b>3. dbt build</b><br>sources pinned with<br>AT (VERSION =&gt; id)",
        "<b>4. Audit</b><br>unique · not_null · grain<br>reconciles with staging",
        "<b>5. Publish</b><br>atomic overwrite of gold<br>+ input ids as lineage",
    ]
    t_cells = [p.step(t, 20 + i * (w + gap + 60), 50, w, h, tr,
                      fill="#FFF8DC" if i == 4 else "#ffffff", stroke="#C9A227" if i == 4 else "#999999")
               for i, t in enumerate(t_labels)]
    for a, b in zip(t_cells, t_cells[1:], strict=False):
        p.edge(a, b, "no" if a == t_cells[1] else ("pass" if a == t_cells[3] else ""))
    skip = p.step("<b>yes → nothing to do</b><br>restart never republishes", 20 + (w + gap + 60), 180, w, 60, tr,
                  fill="#F4F6F8", stroke="#7D8C99")
    keep = p.step("<b>fail → gold unchanged</b><br>API keeps serving the last<br>good mart; retried on new data",
                  20 + 3 * (w + gap + 60), 180, w, 66, tr, fill="#FBEFEF", stroke="#B85450")
    p.edge(t_cells[1], skip, "")
    p.edge(t_cells[3], keep, "")
    return p


def main() -> None:
    pages = [architecture(), semantics()]
    OUT.write_text(
        '<mxfile host="gen.py">' + "".join(pg.xml(f"p{i}") for i, pg in enumerate(pages)) + "</mxfile>\n",
        encoding="utf-8",
    )
    names = ["1-architecture.png", "2-exactly-once.png"]
    PAGES.write_text("".join(f"{n}\t{pg.name}\n" for n, pg in zip(names, pages, strict=True)), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(pages)} pages")


if __name__ == "__main__":
    main()
