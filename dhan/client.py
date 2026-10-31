"""Minimal Dhan API v2 client: auth from env, rate limiting, retries. Read-only market data calls only."""
import os
import threading
import time

import requests

BASE = "https://api.dhan.co/v2"


class Dhan:
    def __init__(self, per_second=4.0):
        self.h = {"access-token": os.environ["DHAN_ACCESS_TOKEN"], "client-id": os.environ["DHAN_CLIENT_ID"],
                  "Content-Type": "application/json", "Accept": "application/json"}
        self.gap = 1.0 / per_second
        self.lock = threading.Lock()
        self.last = 0.0
        self.s = requests.Session()
        self.throttled = 0

    def _wait(self, gap):
        with self.lock:
            now = time.monotonic()
            t = max(self.last + gap, now)
            self.last = t
        time.sleep(max(0.0, t - now))

    def post(self, path, body, gap=None, tries=10):
        """Returns (status, json-or-text). Retries on 429 / 5xx / network errors; 429 backs off 20-60 s,
        because Dhan keeps throttling an account for a while after its rate limit is hit."""
        last = (0, "no response")
        for attempt in range(tries):
            self._wait(gap or self.gap)
            try:
                r = self.s.post(f"{BASE}{path}", headers=self.h, json=body, timeout=60)
            except requests.RequestException as e:
                last = (0, repr(e))
                time.sleep(2 ** min(attempt, 5))
                continue
            if r.status_code == 429:
                self.throttled += 1
                last = (429, r.text[:200])
                time.sleep(min(20 * (attempt + 1), 60))
                continue
            if r.status_code >= 500:
                last = (r.status_code, r.text[:200])
                time.sleep(2 ** min(attempt, 5))
                continue
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, r.text
        return last

    def get(self, path):
        self._wait(self.gap)
        r = self.s.get(f"{BASE}{path}", headers=self.h, timeout=60)
        return r.status_code, r.json()
