#!/usr/bin/env python3
"""给 raindrop-fx 的 bundle/index.js 打一个补丁：换掉细水珠的撒点哈希。

为什么要打
----------
raindrop-fx 在 vertex shader 里这样给每颗细水珠定位：

    float gold_noise(in vec2 xy, in float seed) {
        return fract(tan(distance(xy*PHI, xy)*seed)*xy.x);
    }
    pos.x = gold_noise(vec2(1, id), seed + 1.0)
    pos.y = gold_noise(vec2(id, 1), seed + 2.0)

两个坐标里 distance(...) 的值完全相同（都等于 0.618·√(id²+1)），只有乘的
seed 差了 1。于是对固定的 id，(x, y) 随 seed 变化是在画面上**描一条一维
曲线**，不是撒点。seed 每帧重掷，实例编号 id 恒为 1..N，所以水珠永远落在
那 N 条曲线上。细水珠图层只被经过的水珠擦掉、不会自己淡出，痕迹于是越积
越深——屏幕上看见的那几道贯穿全屏的大斜线就是这么来的。

补丁把这个哈希换成一个 x、y 都混进 id 与 seed 的标准 hash，两个坐标从此
互不相关，撒出来是真正的二维散点。只影响细水珠的分布，别的都不动。

用法
----
    python3 patch_droplet_hash.py bundle/index.js

会先写一份 bundle/index.js.bak，再原地改。重复执行是安全的（已打过会跳过）。
"""

import re
import shutil
import sys
from pathlib import Path

# 目标表达式在 GLSL 里没有换行，打包时不会被转义拆开，可以直接字面匹配。
# 兼容逗号后有无空格两种写法。
PATTERN = re.compile(r"fract\(tan\(distance\(xy\s*\*\s*PHI,\s*xy\)\s*\*\s*seed\)\s*\*\s*xy\.x\)")

REPLACEMENT = "fract(sin(dot(vec3(xy, seed), vec3(12.9898, 78.233, 37.719))) * 43758.5453)"

MARKER = "43758.5453"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"找不到 {path}", file=sys.stderr)
        return 1

    src = path.read_text(encoding="utf-8", errors="surrogateescape")

    if MARKER in src:
        print("已经打过补丁，跳过。")
        return 0

    out, n = PATTERN.subn(REPLACEMENT, src)
    if n == 0:
        print(
            "没找到目标表达式。可能的原因：\n"
            "  · 这不是 raindrop-fx 的 bundle/index.js\n"
            "  · 版本不同，上游改过 gold_noise\n"
            "两种情况都不要硬改，先在文件里搜 gold_noise 看看它现在长什么样。",
            file=sys.stderr,
        )
        return 1

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    path.write_text(out, encoding="utf-8", errors="surrogateescape")
    print(f"替换 {n} 处。原文件已备份到 {backup}")
    print("刷新页面（记得停用缓存或强制刷新）即可看到细水珠变成均匀散点。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
