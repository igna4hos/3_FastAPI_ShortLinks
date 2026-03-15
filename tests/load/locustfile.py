from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, task


class ShortLinksUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self) -> None:
        self.short_codes: list[str] = []

    @task(5)
    def bulk_create_link(self) -> None:
        alias = f"ld{uuid.uuid4().hex[:10]}"
        payload = {
            "original_url": f"https://example.com/load/{alias}",
            "custom_alias": alias,
        }
        with self.client.post(
            "/links/shorten",
            json=payload,
            name="POST /links/shorten",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"unexpected status {response.status_code}")
                return
            self.short_codes.append(alias)
            if len(self.short_codes) > 200:
                self.short_codes = self.short_codes[-200:]

    @task(3)
    def redirect_recent_link(self) -> None:
        if not self.short_codes:
            self.bulk_create_link()
            return

        short_code = random.choice(self.short_codes[-50:])
        with self.client.get(
            f"/{short_code}",
            name="GET /{short_code}",
            allow_redirects=False,
            catch_response=True,
        ) as response:
            if response.status_code != 307:
                response.failure(f"unexpected status {response.status_code}")

    @task(2)
    def fetch_popular_links(self) -> None:
        with self.client.get(
            "/links/popular?limit=10",
            name="GET /links/popular",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"unexpected status {response.status_code}")
