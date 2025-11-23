import json
import os

from shelfspace.apis.base import BaseAPI
from shelfspace.models import LegacyEntry, MediaType, Status


class NotionAPI(BaseAPI):
    base_url = "https://api.notion.com/v1"
    secret = os.environ.get("NOTION_API_KEY")
    target_page = os.environ.get("NOTION_PAGE_ID")
    databases = {}

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.secret}",
            "Notion-Version": "2022-06-28",
        }

    def _paginated_post(self, url, params=None):
        if params is None:
            params = {}
        params["page_size"] = 100
        results = []
        has_more = True
        while has_more:
            result = self._post(url, params)
            results += result["results"]
            has_more = result["has_more"]
            params["start_cursor"] = result.get("next_cursor")

        return results

    def get_children(self, block_id: str):
        return self._get(f"/blocks/{block_id}/children", {"page_size": 100})

    def get_databases(self):
        self.databases = {}

        result = self.get_children(self.target_page)
        for block in result["results"]:
            if block["type"] == "child_database":
                self.databases[block["child_database"]["title"]] = block["id"]

        return self.databases

    def get_objects(self, database_id: str) -> list[LegacyEntry]:
        results = self._paginated_post(f"/databases/{database_id}/query")
        objects = []
        for object_data in results:
            properties = object_data["properties"]
            if not properties["Type"]["select"]:
                continue
            spent = properties["Sp."]["number"]
            estimated = properties["Est."]["number"]
            if not spent:
                status = Status.FUTURE
            elif spent < estimated:
                status = Status.CURRENT
            else:
                status = Status.DONE
            obj = LegacyEntry(
                type=properties["Type"]["select"]["name"],
                name="".join(t["plain_text"] for t in properties["Name"]["title"]),
                notes="".join(
                    t["plain_text"] for t in properties["Notes"]["rich_text"]
                ),
                estimated=estimated,
                spent=spent,
                status=status,
            )
            objects.append(obj)

        return objects

    def get_objects_by_type(self, types: list[MediaType]) -> list[LegacyEntry]:
        result = []
        for db_id in self.databases.values():
            objects = self.objects.get(db_id, [])
            for obj in objects:
                if obj.type in types:
                    result.append(obj)

        return result

    def load_objects(self) -> int:
        self.objects = {}
        total = 0
        for db_id in self.databases.values():
            objects = self.get_objects(db_id)
            self.objects[db_id] = objects
            total += len(objects)
        return total

    def create_object(self, database_id: str, entry: LegacyEntry):
        post_data = {
            "parent": {
                "database_id": database_id,
            },
            "properties": {
                "Type": {
                    "select": {
                        "name": entry.type.value,
                    }
                },
                "Name": {
                    "title": [{"text": {"content": entry.name}}],
                },
                "Notes": {"rich_text": [{"text": {"content": entry.notes}}]},
                "Est.": {
                    "number": entry.estimated,
                },
                "Sp.": {
                    "number": entry.spent,
                },
                "Release": {
                    "rich_text": [{"text": {"content": entry.release_date or ""}}],
                },
                "Rating": {
                    "number": entry.rating,
                },
                "Metainfo": {
                    "rich_text": [
                        {"text": {"content": json.dumps(entry.metadata or {})}}
                    ],
                },
            },
        }
        print(post_data)
        return self._post(
            "/pages",
            post_data,
        )
