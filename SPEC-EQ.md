# 均衡层 · 接口约定

装在渲染谱与扬声器之间。**引擎不动**，`rain_engine.py` 的输出一个字节都没变，
EQ 只读渲染谱里已有的 `intensity` 与 `character`。

---

## 1. 为什么是 EQ，不是继续调音量

雨的强弱在听感上主要是**频谱**的事。毛毛雨是离散水滴撞击，能量集中在
2–8 kHz；暴雨是整片水，100–600 Hz 有一层"轰"，反而把高频盖掉一部分。

库里只有四条雨。光靠交叉淡入淡出，中间地带全是同一条素材在不同音量下重复，
听久了就是"同一段雨忽大忽小"。把频谱随 `intensity` 倾斜之后，同一条素材
能撑开成一大片听感。实测这条曲线在 intensity 0→1 之间：

| intensity | 40–160 Hz | 4k–16k | 倾斜 |
|---|---|---|---|
| 0.00 | −16.1 dB | +1.1 dB | **−17.2 dB** |
| 0.50 | −4.1 dB | −0.1 dB | −4.0 dB |
| 1.00 | +2.6 dB | −2.0 dB | **+4.5 dB** |

频谱倾斜摆动 21.7 dB，而宽带响度只动 ±0.75 dB。**音色变了，音量没变。**

---

## 2. 信号图

```
雨六条 stem ─► rainIn ─►[高通 低架 400 3.2k 9k架 character]─► makeup ─► duck ─┐
                                                                              ├─►[室内低通 低架 补偿]─► out
风两条 stem ─► windIn ─►[高通55 1.6k−3dB]                            ─────────┘

每次雷 ─►[低通(远近) 低架]─► out        同时触发 duck 把雨压一下
```

节点总数 11 个 BiquadFilter，对 Web Audio 是零负担。

---

## 3. 前端契约

```html
<script src="rain_eq.js"></script>
```

```js
const eq = await RainEQ.create(ctx, eqProfileJson);
rainStemGain.connect(eq.rainIn);      // 四条雨
windStemGain.connect(eq.windIn);      // 两条风
eq.out.connect(masterGain);

// 每帧一次，参数直接来自渲染谱
eq.setFrame(frame.intensity, frame.character, when, rampSeconds);

// 用户滑杆，与气象数据无关
eq.setIndoor(0.35);

// 每次雷
const th = eq.thunderNode(strike.gain);
src.connect(gain).connect(pan).connect(th.input);
th.output.connect(masterGain);
eq.duck(strike.gain, when);

eq.setEnabled(false);   // 旁路，回到引擎原始输出
```

`eq_profile.json` 里 21 档 intensity 的滤波器参数，`setFrame` 在档间线性插值，
所有参数用 `setTargetAtTime` 平滑，不会扫出"唰"声。

---

## 4. 各段在做什么

| 段 | 位置 | 随强度 | 理由 |
|---|---|---|---|
| 高通 | 150 → 30 Hz | 越大越低 | 轻雨剥掉低频显得薄而远，大雨放行让"轰"出来 |
| 低架 | 180 Hz | −5 → +3.5 dB | 身量 |
| 峰 | 400 Hz | −4.5 → −2 dB | 箱声。录音机总是贴着某个面，几乎每条雨声素材都有 |
| 峰 | 3.2 kHz | +3 → −0.5 dB | 滴答声的位置。轻雨要亮，大雨要收，否则像白噪声 |
| 高架 | 9 kHz | −1.5 → −4 dB | 嘶声。这东西要连开几小时，高频过量最先让人累 |
| 峰 | 5 kHz | 随 character 0 → +1.5 dB | 阵雨水花更碎更亮 |

**室内感**是用户滑杆，0 户外 / 1 隔窗：低通 20 kHz → 500 Hz，
220 Hz 低架 +4 dB（玻璃过低频挡高频），补偿 +4.5 dB。这是长时间听雨最常被要的一个开关。

**雷的远近**本质是低通——空气对高频的吸收随距离急剧上升。
`gain` 当作远近代理，低通 600 Hz（远）→ 8000 Hz（近），远雷再加 90 Hz 低架 +3 dB。
这一步实际把素材库撑大了一倍：十条"干雷"是近距离的脆响，经低通之后也能当远雷用。

**Ducking**：雷响时把雨压 0 → −3 dB，40 ms 起、600 ms 回。
雷不用调更响，分量自己就出来了。

---

## 5. 响度中性怎么保证的

低架抬 3.5 dB 会让暴雨比引擎设定的更响，等于偷偷改了强度标定。
所以 `make_eq_profile.py` 把每档 intensity 下整条链的宽带增益算出来，
反向写进 `makeup_db`。

加权用 **ITU-R BS.1770 的 K 加权**，不是裸能量积分。这一点很要紧：
人耳对 150 Hz 以下的雨声几乎不计入响度，裸能量积分会把"切掉低频"
误判成掉了 10 dB，补偿就会补过头，轻雨会变成一片高频嘶响。
换成 K 加权之后补偿量落在 +0.75 … +0.00 dB，这才是正常数量级。

---

## 6. 用真实素材重新标定

出货的 `eq_profile.json` 用的是雨声的经验谱型。拿到你自己的素材后跑一次：

```
python make_eq_profile.py --sound sound
```

它会从 `sound/Rain/` 里各取 30 秒，算 1/3 倍频程平均谱，
用实测谱替换经验谱型重算补偿。曲线形状不变，只有 `makeup_db` 会微调。

**注意**：我这边验证时容器里的素材是合成占位（频谱是平的，真实雨声不会这样），
所以任何"实测"数字都不作数。曲线形状与响度中性的机制是验证过的，
`makeup_db` 的绝对值要以你本机跑出来的为准。

---

## 7. 与体检层的关系

`analyze_assets.py` 解决的是"素材本身有低谷，听起来像雨停了"；
EQ 解决的是"四条素材撑不满强度范围"。两件事互不干涉：

- 体检 → `assets_profile.json` → `rain_engine.py --profile` → 渲染谱里带 `regions`
- EQ → `eq_profile.json` → 前端 `rain_eq.js` → 不经过引擎

想回到没有 EQ 的状态，`eq.setEnabled(false)` 即可，渲染谱本身没有任何改动。
