#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rain_select.py — 三种模式的选曲逻辑

rain_engine.py 产出的是静态数据；这个模块负责「此刻该播哪一场雨」。
用法：包一层 HTTP 接口，或在构建期把结果烤成静态 JSON。

    eng = Engine("web_out")
    pick = eng.pick_random(history={"singapore": 2})
    pick = eng.pick_city_any("hangzhou")
    pick = eng.pick_city_date("hangzhou", "2026-04-12")
    nxt  = eng.next_after("hangzhou", 17)

history 是「用户听过的城市 -> 次数」，由前端 localStorage 保存后回传；
指定日期模式不读它。
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone

DEWEIGHT_DECAY = 0.5     # 听过一次，权重乘 0.5
DEWEIGHT_FLOOR = 0.05    # 权重下限，保证听过的城市仍有机会


class NoCandidate(Exception):
    """该条件下没有可播的雨。异常里带够前端出文案的信息。"""

    def __init__(self, reason, **info):
        super().__init__(reason)
        self.reason = reason
        self.info = info


class Engine:
    def __init__(self, root, rng=None):
        self.root = root
        self.rng = rng or random.Random()
        with open(os.path.join(root, "manifest.json"), "r", encoding="utf-8") as f:
            self.manifest = json.load(f)
        with open(os.path.join(root, "index", "cities.json"), "r", encoding="utf-8") as f:
            self.roster = {c["slug"]: c for c in json.load(f)["cities"]}
        self._city_cache = {}
        self._hour_cache = {}

    # ------------------------------------------------------------ 读取

    def city(self, slug):
        if slug not in self._city_cache:
            path = os.path.join(self.root, "index", "city", "%s.json" % slug)
            if not os.path.exists(path):
                raise NoCandidate("unknown_city", slug=slug)
            with open(path, "r", encoding="utf-8") as f:
                self._city_cache[slug] = json.load(f)
        return self._city_cache[slug]

    def hour_pool(self, h):
        if h not in self._hour_cache:
            with open(os.path.join(self.root, "index", "hour", "%02d.json" % h),
                      "r", encoding="utf-8") as f:
                self._hour_cache[h] = json.load(f)
        return self._hour_cache[h]

    def event(self, slug, event_id):
        for e in self.city(slug)["events"]:
            if e["event_id"] == event_id:
                return e
        raise NoCandidate("unknown_event", slug=slug, event_id=event_id)

    # ------------------------------------------------------------ 时间

    @staticmethod
    def _now(now_utc):
        return now_utc or datetime.now(timezone.utc)

    def _cue(self, slug, event_id, frame, now):
        """把选中的 (事件, 帧) 变成前端可直接起播的指令。"""
        ev = self.event(slug, event_id)
        return {
            "city": slug,
            "city_name": self.roster[slug]["name"],
            "timezone": self.roster[slug]["timezone"],
            "event_id": event_id,
            "frame_index": frame,
            "second_offset": now.minute * 60 + now.second,
            "score_url": ev["score"],
            "event": ev,
            "served_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # ------------------------------------------------------------ 模式一：全局随机

    def pick_random(self, now_utc=None, history=None):
        now = self._now(now_utc)
        pool = self.hour_pool(now.hour)["cities"]
        if not pool:
            raise NoCandidate("no_rain_anywhere_at_hour", hour_utc=now.hour)
        slug = self._weighted_city(list(pool.keys()), history or {})
        eid, frame = self.rng.choice(pool[slug])
        cue = self._cue(slug, eid, frame, now)
        cue["mode"] = "random"
        return cue

    # ------------------------------------------------------------ 模式二：指定城市 + 「我不在乎」

    def pick_city_any(self, slug, now_utc=None, history=None):
        now = self._now(now_utc)
        doc = self.city(slug)
        cands = doc["hour_of_day"].get(str(now.hour), [])
        if cands:
            eid, frame = self.rng.choice(cands)
            cue = self._cue(slug, eid, frame, now)
            cue["mode"] = "city_any"
            cue["hour_aligned"] = True
            return cue
        # 该城在这个 UTC 钟点从未下过雨：放弃钟点对齐，随便给一场，并交代原因
        if not doc["events"]:
            raise NoCandidate("city_has_no_rain", slug=slug,
                              coverage_days=doc["coverage_utc"]["days"])
        ev = self.rng.choice(doc["events"])
        cue = self._cue(slug, ev["event_id"], 0, now)
        cue["mode"] = "city_any"
        cue["hour_aligned"] = False
        cue["notice"] = {
            "code": "no_rain_at_this_hour",
            "hour_utc": now.hour,
            "coverage_days": doc["coverage_utc"]["days"],
            # 前端文案：过去 {coverage_days} 天里，这座城市在此刻还未下过雨
        }
        return cue

    # ------------------------------------------------------------ 模式三：指定城市 + 指定日期

    def pick_city_date(self, slug, date_str, now_utc=None, use_utc_date=False):
        now = self._now(now_utc)
        doc = self.city(slug)
        table = doc["dates_utc"] if use_utc_date else doc["dates_local"]
        ids = table.get(date_str)
        if not ids:
            raise NoCandidate("no_rain_on_date", slug=slug, date=date_str,
                              available=sorted(table.keys()))
        # 尽量对齐 UTC 钟点；这一天的事件覆盖不到当前钟点就从头播
        frame, chosen = 0, ids[0]
        for eid in ids:
            for cand_eid, cand_frame in doc["hour_of_day"].get(str(now.hour), []):
                if cand_eid == eid:
                    chosen, frame = eid, cand_frame
                    break
            else:
                continue
            break
        cue = self._cue(slug, chosen, frame, now)
        cue["mode"] = "city_date"
        cue["date"] = date_str
        cue["date_basis"] = "utc" if use_utc_date else "city_local"
        cue["events_on_date"] = ids
        cue["loop"] = True          # 本模式播完循环，不换城
        cue["hour_aligned"] = frame != 0
        return cue

    # ------------------------------------------------------------ 结束之后

    def next_after(self, slug, event_id):
        """『停留』按钮用：这场雨下完，同城下一场在多久之后。"""
        ev = self.event(slug, event_id)
        if ev["gap_to_next_hours"] is None:
            return {"has_next": False,
                    "hours_to_coverage_end": ev["hours_to_coverage_end"],
                    "notice": "no_more_rain_in_coverage"}
        return {"has_next": True,
                "wait_hours": ev["gap_to_next_hours"],
                "next_event_id": event_id + 1}

    def next_city(self, now_utc=None, history=None, exclude=()):
        """降水结束后自动换城：找此刻有雨的另一座城。"""
        now = self._now(now_utc)
        pool = {k: v for k, v in self.hour_pool(now.hour)["cities"].items()
                if k not in exclude}
        if not pool:
            raise NoCandidate("no_rain_anywhere_at_hour", hour_utc=now.hour)
        slug = self._weighted_city(list(pool.keys()), history or {})
        eid, frame = self.rng.choice(pool[slug])
        cue = self._cue(slug, eid, frame, now)
        cue["mode"] = "random"
        cue["gap_ms"] = self.manifest["playback"]["city_switch_gap_ms"]
        return cue

    # ------------------------------------------------------------ 降权池

    def _weighted_city(self, slugs, history):
        """城市层面等权，不按事件数等权——否则新加坡、重庆会淹掉银川。
        听过的城市按次数指数降权，未听过的优先。"""
        weights = []
        for s in slugs:
            n = history.get(s, 0)
            weights.append(max(DEWEIGHT_DECAY ** n, DEWEIGHT_FLOOR))
        total = sum(weights)
        r = self.rng.random() * total
        acc = 0.0
        for s, w in zip(slugs, weights):
            acc += w
            if r <= acc:
                return s
        return slugs[-1]


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="选曲逻辑自检")
    ap.add_argument("--root", default="web_out")
    ap.add_argument("--mode", default="random", choices=("random", "city_any", "city_date"))
    ap.add_argument("--city", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--at", default=None, help="模拟 UTC 时刻，如 2026-05-03T14:37")
    a = ap.parse_args()

    eng = Engine(a.root)
    now = datetime.fromisoformat(a.at).replace(tzinfo=timezone.utc) if a.at else None
    if a.mode == "random":
        out = eng.pick_random(now)
    elif a.mode == "city_any":
        out = eng.pick_city_any(a.city, now)
    else:
        out = eng.pick_city_date(a.city, a.date, now)
    print(json.dumps(out, ensure_ascii=False, indent=2))
