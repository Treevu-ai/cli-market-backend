"""Semantic moat layer for dashboard view model (Golden Record linkage)."""

from __future__ import annotations


def _fmt_int(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".")


def apply_semantic_moat_blocks(view: dict, data: dict) -> dict:
    """Inject semantic index KPIs into dashboard_view.blocks."""
    kpis = data.get("kpis") or {}
    linked = int(kpis.get("snapshots_linked") or 0)
    distinct = int(kpis.get("golden_records_distinct") or 0)
    registry = int(kpis.get("golden_records_registry") or 0)
    unlinked = int(kpis.get("unlinked_snapshots") or 0)
    linkage_pct = float(kpis.get("linkage_pct") or 0)
    total = int(kpis.get("total_indexed") or 0)

    blocks = view.setdefault("blocks", {})
    blocks["semantic_moat"] = {
        "id": "semantic_moat",
        "title": "Semantic Moat — Golden Records",
        "intro": (
            "Cada precio de tienda se vincula a un Golden Record estable (prod_*). "
            "Eso permite comparar el mismo producto aunque las tiendas lo nombren distinto."
        ),
        "metrics": {
            "snapshots_linked": linked,
            "golden_records_distinct": distinct,
            "golden_records_registry": registry,
            "unlinked_snapshots": unlinked,
            "linkage_pct": linkage_pct,
            "total_indexed": total,
        },
        "cards": [
            {
                "label": "LINKED",
                "headline": f"{_fmt_int(linked)} snapshots con UPID",
                "subline": f"{linkage_pct:.1f}% del inventario",
            },
            {
                "label": "GOLDEN RECORDS",
                "headline": f"{_fmt_int(registry)} en registry · {_fmt_int(distinct)} distintos en DB",
                "subline": "cli-market-index persistent store",
            },
            {
                "label": "PENDING",
                "headline": f"{_fmt_int(unlinked)} sin vincular",
                "subline": "ops/backfill_index.py o próximo ciclo collector",
            },
        ],
        "progress_pct": linkage_pct,
        "source": "kpis.snapshots_linked, golden_records_*, linkage_pct",
    }

    portada = blocks.get("portada") or {}
    cards = list(portada.get("cards") or [])
    cards.append(
        {
            "label": "SEMANTIC",
            "headline": f"{linkage_pct:.1f}% linked · {_fmt_int(registry)} Golden Records",
            "subline": "canonical_product_id + index registry",
            "source": "kpis.linkage_pct, golden_records_registry",
        }
    )
    portada["cards"] = cards
    blocks["portada"] = portada

    moat = blocks.get("moat_narrative") or {}
    pillars = list(moat.get("pillars") or [])
    pillars.append(
        {
            "title": "Semántica",
            "headline": f"{linkage_pct:.0f}% vinculado a prod_*",
            "microcopy": f"{_fmt_int(distinct)} productos canónicos deduplicados entre tiendas.",
            "source": "kpis.linkage_pct, golden_records_distinct",
        }
    )
    moat["pillars"] = pillars
    blocks["moat_narrative"] = moat

    return view