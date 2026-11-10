"""A small in-memory Homebox for tool-level tests.

It answers the endpoints the tools call and keeps state between requests, so
a test can run a tool and then assert on what the server ended up holding.
The behaviours that matter are modelled on Homebox 0.26:

* PUT /entities/{id} REPLACES the entity. A field the body omits is cleared.
  This is the "PUT-clears gotcha" every write tool has to survive, so a tool
  that forgets to preserve a field fails its test here.
* PATCH /entities/{id} merges, leaving omitted fields alone.
* GET /entities returns items only (never locations), hides archived items
  unless includeArchived=true, ORs repeated parentIds, and ANDs the filters.
* List rows are lightweight: no `fields`, empty `assetId`.

Every request is recorded in `calls` as (method, path).
"""
from __future__ import annotations

import json
import re

import httpx

ITEM_TYPE = {"id": "type-item", "name": "Item", "isLocation": False}
LOCATION_TYPE = {"id": "type-loc", "name": "Location", "isLocation": True}
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# what a PUT clears when the body omits it
_SCALARS = {
    "description": "", "quantity": 1, "insured": False, "archived": False,
    "manufacturer": "", "modelNumber": "", "serialNumber": "", "notes": "",
    "purchasePrice": 0, "purchaseFrom": "", "purchaseDate": "",
    "soldDate": "", "soldTo": "", "soldPrice": 0, "soldNotes": "",
    "warrantyExpires": "", "warrantyDetails": "", "lifetimeWarranty": False,
}


class FakeHomebox:
    def __init__(self, router, version: str = "v0.26.2"):
        self.version = version
        self.entities: dict[str, dict] = {}
        self.tags: dict[str, dict] = {}
        self.files: dict[tuple[str, str], bytes] = {}
        self.maintenance: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.requests: list[httpx.Request] = []
        self._seq = 0
        router.route(url__regex=r".*").mock(side_effect=self._dispatch)

    # -- seeding ---------------------------------------------------------
    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def add_tag(self, name: str, **extra) -> dict:
        tag = {"id": self._next("tag"), "name": name, "description": "",
               "color": "", "icon": "", "parentId": NIL_UUID, **extra}
        self.tags[tag["id"]] = tag
        return tag

    def add_location(self, name: str, parent: dict | None = None, **extra) -> dict:
        return self._add(name, LOCATION_TYPE, parent, extra)

    def add_item(self, name: str, parent: dict | None = None, **extra) -> dict:
        return self._add(name, ITEM_TYPE, parent, extra)

    def _add(self, name, etype, parent, extra) -> dict:
        eid = extra.pop("id", None) or self._next("e")
        tags = [self._tag_named(t) for t in extra.pop("tags", [])]
        entity = {
            "id": eid, "name": name, "assetId": "", "entityType": etype,
            "parent": {"id": parent["id"], "name": parent["name"]} if parent else None,
            "tags": tags, "fields": [], "attachments": [],
            **_SCALARS, **extra,
        }
        self.entities[eid] = entity
        return entity

    def _tag_named(self, name: str) -> dict:
        for t in self.tags.values():
            if t["name"] == name:
                return {"id": t["id"], "name": t["name"]}
        t = self.add_tag(name)
        return {"id": t["id"], "name": t["name"]}

    def add_attachment(self, entity: dict, title: str, content: bytes = b"data",
                       type: str = "manual", mime: str = "application/pdf",
                       primary: bool = False) -> dict:
        att = {"id": self._next("att"), "title": title, "type": type,
               "primary": primary, "mimeType": mime,
               "createdAt": "2026-01-01T00:00:00Z"}
        entity["attachments"].append(att)
        self.files[(entity["id"], att["id"])] = content
        return att

    # -- helpers ---------------------------------------------------------
    def called(self, method: str, path_regex: str) -> list[tuple[str, str]]:
        return [c for c in self.calls
                if c[0] == method and re.fullmatch(path_regex, c[1])]

    def body(self, method: str, path_regex: str) -> dict:
        """JSON body of the last matching request."""
        for req in reversed(self.requests):
            path = req.url.path.removeprefix("/api/v1")
            if req.method == method and re.fullmatch(path_regex, path):
                return json.loads(req.content)
        raise AssertionError(f"no {method} {path_regex} request was made")

    def _is_loc(self, e: dict) -> bool:
        return e["entityType"]["isLocation"]

    def _path(self, e: dict) -> list[dict]:
        nodes = [{"id": e["id"], "name": e["name"]}]
        while e.get("parent"):
            e = self.entities[e["parent"]["id"]]
            nodes.insert(0, {"id": e["id"], "name": e["name"]})
        return nodes

    def _tree(self, parent_id: str | None) -> list[dict]:
        return [{"id": e["id"], "name": e["name"], "type": "location",
                 "children": self._tree(e["id"])}
                for e in self.entities.values()
                if self._is_loc(e) and (e["parent"] or {}).get("id") == parent_id]

    def _apply(self, entity: dict, body: dict) -> None:
        """Copy the mutable keys of a PUT/PATCH body onto an entity."""
        for k, v in body.items():
            if k == "tagIds":
                entity["tags"] = [{"id": t, "name": self.tags[t]["name"]} for t in v]
            elif k == "parentId":
                p = self.entities[v]
                entity["parent"] = {"id": p["id"], "name": p["name"]}
            elif k == "entityTypeId":
                entity["entityType"] = (LOCATION_TYPE if v == LOCATION_TYPE["id"]
                                        else ITEM_TYPE)
            elif k != "id":
                entity[k] = v

    # -- dispatch --------------------------------------------------------
    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        method = request.method
        self.calls.append((method, path))
        self.requests.append(request)
        is_json = "json" in request.headers.get("content-type", "")
        body = json.loads(request.content) if request.content and is_json else {}
        params = request.url.params

        def ok(payload=None, status=200):
            return httpx.Response(status, json=payload if payload is not None else {})

        if path == "/status":
            return ok({"build": {"version": self.version}})
        if path == "/entity-types":
            return ok([ITEM_TYPE, LOCATION_TYPE])
        if path == "/entities/tree":
            return ok(self._tree(None))

        if path == "/entities" and method == "GET":
            return ok(self._search(params))
        if path == "/entities" and method == "POST":
            etype = (LOCATION_TYPE if body.get("entityTypeId") == LOCATION_TYPE["id"]
                     else ITEM_TYPE)
            parent = self.entities.get(body.get("parentId"))
            e = self._add(body["name"], etype, parent, {})
            return ok({"id": e["id"]}, 201)
        if path == "/entities/import":
            return httpx.Response(204)

        if m := re.fullmatch(r"/assets/(.+)", path):
            ref = m.group(1).replace("-", "")
            hits = [{"id": e["id"]} for e in self.entities.values()
                    if e["assetId"] and e["assetId"].replace("-", "") == ref]
            return ok({"items": hits})

        if m := re.fullmatch(r"/entities/([^/]+)/path", path):
            return ok(self._path(self.entities[m.group(1)]))

        if m := re.fullmatch(r"/entities/([^/]+)/attachments", path):
            e = self.entities[m.group(1)]
            form = request.content.decode("latin-1")
            def part(name):
                hit = re.search(rf'name="{name}"\r\n\r\n(.*?)\r\n', form, re.S)
                return hit.group(1) if hit else None
            att = self.add_attachment(
                e, part("name"), type=part("type"), primary=part("primary") == "true")
            return ok(att, 201)

        if m := re.fullmatch(r"/entities/([^/]+)/attachments/([^/]+)", path):
            e = self.entities[m.group(1)]
            att = next(a for a in e["attachments"] if a["id"] == m.group(2))
            if method == "GET":
                return httpx.Response(200, content=self.files[(e["id"], att["id"])],
                                      headers={"content-type": att["mimeType"]})
            if method == "PUT":
                att.update(body)
                return ok(att)
            e["attachments"].remove(att)
            return httpx.Response(204)

        if m := re.fullmatch(r"/entities/([^/]+)", path):
            e = self.entities.get(m.group(1))
            if e is None:
                return httpx.Response(404, json={"error": "not found"})
            if method == "GET":
                return ok(e)
            if method == "PUT":
                # PUT replaces: everything the body omits goes back to empty
                e.update(_SCALARS)
                e.update({"tags": [], "fields": [], "parent": None, "assetId": ""})
                self._apply(e, body)
                return ok(e)
            if method == "PATCH":
                self._apply(e, body)
                return ok(e)
            del self.entities[e["id"]]
            return httpx.Response(204)

        if path == "/tags" and method == "GET":
            return ok(list(self.tags.values()))
        if path == "/tags" and method == "POST":
            return ok(self.add_tag(body["name"]), 201)
        if m := re.fullmatch(r"/tags/([^/]+)", path):
            if method == "PUT":
                self.tags[m.group(1)].update(body)
                return ok(self.tags[m.group(1)])
            del self.tags[m.group(1)]
            return httpx.Response(204)

        if path.startswith("/actions/"):
            return ok({"completed": 1})
        if path.startswith("/labelmaker/"):
            return httpx.Response(200, content=b"\x89PNG-label")
        if path == "/qrcode":
            return httpx.Response(200, content=b"JPEG-qr")
        if path == "/products/search-from-barcode":
            return ok([{"barcode": params.get("data"), "item": {"name": "Widget"}}])

        return httpx.Response(500, json={"error": f"fake has no {method} {path}"})

    def _search(self, params) -> dict:
        q = (params.get("q") or "").lower()
        parents = params.get_list("parentIds")
        tags = params.get_list("tags")
        archived = params.get("includeArchived") == "true"
        hits = []
        for e in self.entities.values():
            if self._is_loc(e):
                continue
            if e["archived"] and not archived:
                continue
            if q and q not in e["name"].lower() and q not in e["description"].lower():
                continue
            if parents and (e["parent"] or {}).get("id") not in parents:
                continue
            if tags and not set(tags) <= {t["id"] for t in e["tags"]}:
                continue
            # list rows are lightweight: no fields, no assetId
            hits.append({"id": e["id"], "name": e["name"], "assetId": "",
                         "archived": e["archived"], "entityType": e["entityType"],
                         "parent": e["parent"], "quantity": e["quantity"]})
        page = int(params.get("page", 1))
        size = int(params.get("pageSize", 200))
        return {"items": hits[(page - 1) * size: page * size], "total": len(hits)}
