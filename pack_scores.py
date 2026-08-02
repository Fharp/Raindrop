#!/usr/bin/env python3
"""把 web_out/scores/<slug>/<n>.json 合并成每城若干个包。

    python pack_scores.py                    # 就地改 web_out
    python pack_scores.py --dry-run          # 只报数，不动文件
    python pack_scores.py --max-kb 160       # 调包的大小上限

为什么要做这件事：Cloudflare Pages 免费版每站点最多 20000 个文件。
当前布局是一场雨一个 score 文件，实测每城约 121 个，
也就是**约 165 座城就顶到天花板**——这跟 Open-Meteo 的额度毫无关系，
纯粹是文件个数。合并之后每城 1 到几个包，
20000 个文件能装几千座城，这道墙就不存在了。

包的大小有上限而不是「一城一包」，是因为播一场雨要把整个包下下来。
一城一包的话，新加坡那种 343 场的城要先下 850 KB 才出声。
按 --max-kb 切开，代价回到和现在同一个量级，
而且数据往后扩（多拉几年）时它会自己多切几块，不用再调。

包的形状：{"<event_id>": {原样的 score 对象}, …}
score 对象一个字节都不改，客户端拿到之后与拆开时完全一致。

配套改动：rain_audio.js 的 loadEvent 会看 cityDoc.pack_version，
有就从包里取，没有就按老路径取——没打包的 web_out 仍然能跑。

幂等：已经打过包的 web_out 再跑一次不会重复处理。
"""

import argparse
import json
import os
import shutil
import sys

PACK_VERSION = 1
PAGES_FREE_FILE_LIMIT = 20000


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def count_files(root):
    return sum(len(fs) for _, _, fs in os.walk(root))


def pack_city(web_out, slug, max_bytes, dry):
    """返回 (包数, 原文件数, 字节数, 路径映射 old->new)。已打过包返回 None。"""
    doc_path = os.path.join(web_out, "index", "city", slug + ".json")
    if not os.path.exists(doc_path):
        return None
    doc = load(doc_path)
    if doc.get("pack_version"):
        return None
    events = doc.get("events") or []
    if not events:
        return None

    # 按 event_id 顺序装桶，装到超过上限就换下一个。
    # 不按大小排序：同一场附近的雨往往属于同一段天气，
    # 顺序装桶让「换下一场」大概率还落在同一个包里，少一次下载。
    buckets, cur, cur_bytes = [], [], 0
    mapping = {}
    total_bytes = 0
    old_files = []

    for ev in events:
        rel = ev.get("score")
        if not rel:
            continue
        src = os.path.join(web_out, rel.replace("/", os.sep))
        if not os.path.exists(src):
            print("    ! 缺文件 %s，这一场跳过" % rel)
            continue
        raw = open(src, "rb").read()
        old_files.append(src)
        total_bytes += len(raw)
        if cur and cur_bytes + len(raw) > max_bytes:
            buckets.append(cur)
            cur, cur_bytes = [], 0
        cur.append((ev, json.loads(raw.decode("utf-8"))))
        cur_bytes += len(raw)
    if cur:
        buckets.append(cur)
    if not buckets:
        return None

    out_dir = os.path.join(web_out, "scores", slug)
    for k, bucket in enumerate(buckets):
        rel_new = "scores/%s/p%d.json" % (slug, k)
        blob = {str(ev["event_id"]): score for ev, score in bucket}
        if not dry:
            os.makedirs(out_dir, exist_ok=True)
            dump(os.path.join(web_out, "scores", slug, "p%d.json" % k), blob)
        for ev, _ in bucket:
            mapping[ev["score"]] = rel_new
            ev["score"] = rel_new

    doc["pack_version"] = PACK_VERSION
    if not dry:
        dump(doc_path, doc)
        for src in old_files:
            os.remove(src)

    return len(buckets), len(old_files), total_bytes, mapping


def rewrite_index(web_out, mapping, dry):
    """index/ 下面别的地方也可能引用旧的 score 路径（比如按钟点的索引），
    统一改掉。改不到的引用会在客户端表现为 404 回落成网页，很难查。"""
    hit = 0

    def walk(node):
        nonlocal hit
        if isinstance(node, dict):
            v = node.get("score")
            if isinstance(v, str) and v in mapping:
                node["score"] = mapping[v]
                hit += 1
            for x in node.values():
                walk(x)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    idx = os.path.join(web_out, "index")
    for root, _, files in os.walk(idx):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            p = os.path.join(root, fn)
            if os.path.dirname(p) == os.path.join(idx, "city"):
                continue                    # 城市索引在 pack_city 里已经改过
            obj = load(p)
            before = hit
            walk(obj)
            if hit > before and not dry:
                dump(p, obj)
    return hit


def main():
    ap = argparse.ArgumentParser(description="合并 score 文件，适配 Pages 的文件数上限")
    ap.add_argument("--web-out", default="web_out")
    ap.add_argument("--max-kb", type=int, default=160,
                    help="单个包的大小上限（未压缩）。播一场雨要下整个包，别调太大")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    web_out = args.web_out
    if not os.path.isdir(web_out):
        sys.exit("找不到目录 %s" % web_out)
    roster_path = os.path.join(web_out, "index", "cities.json")
    if not os.path.exists(roster_path):
        sys.exit("找不到 %s，先跑 rain_engine.py" % roster_path)

    before = count_files(web_out)
    roster = load(roster_path)
    slugs = [c["slug"] for c in roster.get("cities", [])]
    print("web_out 现有 %d 个文件，%d 座城" % (before, len(slugs)))
    if args.dry_run:
        print("（--dry-run：只报数，不动文件）")
    print()

    mapping, packs, packed_cities, bytes_total = {}, 0, 0, 0
    skipped = 0
    for slug in slugs:
        r = pack_city(web_out, slug, args.max_kb * 1024, args.dry_run)
        if r is None:
            skipped += 1
            continue
        n_pack, n_old, n_bytes, m = r
        mapping.update(m)
        packs += n_pack
        packed_cities += 1
        bytes_total += n_bytes
        print("  %-14s %3d 场 → %d 个包   %7.1f KB" % (slug, n_old, n_pack, n_bytes / 1024))

    if skipped:
        print("\n  %d 座城跳过（没有场次，或已经打过包）" % skipped)

    if mapping:
        hit = rewrite_index(web_out, mapping, args.dry_run)
        if hit:
            print("  index/ 下另有 %d 处引用一并改掉" % hit)

    if not args.dry_run:
        # 空目录留着会让 git 和部署工具都困惑
        for slug in slugs:
            d = os.path.join(web_out, "scores", slug)
            if os.path.isdir(d) and not os.listdir(d):
                shutil.rmtree(d)

    after = count_files(web_out) if not args.dry_run else (
        before - sum(1 for _ in mapping) + packs)

    print("\n" + "=" * 68)
    print("文件数 %d → %d" % (before, after))
    if packed_cities:
        per = after / packed_cities
        print("每城约 %.1f 个文件，包平均 %.0f KB"
              % (per, bytes_total / max(1, packs) / 1024))
        print("按这个密度，20000 个文件的上限约合 %d 座城" % int(PAGES_FREE_FILE_LIMIT / per))
    if args.dry_run:
        print("\n没有真的改动。去掉 --dry-run 再跑一次。")
    else:
        print("\n下一步：客户端要用带 pack 支持的 rain_audio.js，否则会去取已经删掉的旧路径。")


if __name__ == "__main__":
    main()
