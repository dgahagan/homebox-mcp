"""Pagination: _search_all must follow pages until `total` is reached."""
from __future__ import annotations

import httpx


def test_search_all_follows_three_pages(router, good_status):
    all_items = [{"id": f"e{i}"} for i in range(450)]

    def pages(request):
        page = int(request.url.params.get("page", "1"))
        size = int(request.url.params.get("pageSize", "200"))
        start = (page - 1) * size
        return httpx.Response(
            200, json={"items": all_items[start:start + size], "total": len(all_items)}
        )

    route = router.get("/entities").mock(side_effect=pages)

    import homebox_mcp as hb
    got = hb._search_all()
    assert len(got) == 450
    assert route.call_count == 3  # 200 + 200 + 50


def test_search_all_single_page(router, good_status):
    all_items = [{"id": f"e{i}"} for i in range(5)]
    route = router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": all_items, "total": 5})
    )
    import homebox_mcp as hb
    got = hb._search_all()
    assert len(got) == 5
    assert route.call_count == 1
