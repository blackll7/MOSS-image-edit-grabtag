# 弱描述让参考补位（3 seed 实测）

一句话：**提示词没写到的细节处，参考图会把自己的局部特征补进去；写到了，参考就失去位置。**

这不是"参考没用"，而是参考的**作用域由提示词的信息缺口决定**。

## 结论先摆

判据取二值：**小腿上有没有菱形网格护腿**。

| seed | 挂参考 | 不挂参考 |
|---|---|---|
| 8002 | **有**（深色菱格网，踝 → 小腿肚） | 无 |
| 8003 | **有**（菱格网，踝 → 小腿中段） | 无 |
| 8004 | **有**（菱格带箍住小腿） | 无 |

**3 / 3 对 0 / 3。**

提示词里 `#cfe6f2` 那条只写了 `pale blue gradient harem trousers`，**一个腿部装饰词都没有**。参考图的裤脚有一圈深蓝金边菱形装饰，三张挂参考的图都把它延展成了小腿网格；三张不挂参考的都没有，裤脚只是素净收口。

## 为什么这轮可信

- **同 seed 配对**（8002↔8002、8003↔8003、8004↔8004），排除种子噪声；
- 判据是**二值**，不是"像不像"；
- 特征是描述里**明确没写**的，所以不可能是提示词自己画出来的；
- 3 个 seed 方向一致，无反例。

## 反过来：描述写全，参考就被稀释

同一张参考、同一套颜色键，只把 `#cfe6f2` 那句从

```
pale blue gradient harem trousers
```

改成

```
pale blue gradient harem trousers, gathered at the ankle,
with navy-and-gold diamond ornaments around each cuff
```

挂参考 / 不挂参考两臂的**裤脚菱形全部都有**，肉眼几乎无从分辨——判据当场失效。

所以：

> **描述强度与参考影响力是此消彼长的。写全了，tag 就被稀释；留白处，才是参考的地盘。**

## 可复现命令

```powershell
# 挂参考
python moss_run.py --port 8190 `
  --image 'v2sop_ref.png:raw' --color-map 'regcap_gt.png' --depth-map '<abs>\v2sop_depth.png' `
  --control-strength 0.75 --edit-format ai-toolkit --no-dype --no-fast --steps 24 --cfg 3.5 `
  --prompt-file '<prompt>.txt' --w 1216 --h 1632 --seed 8002 --prefix thinR8002

# 不挂参考（对照）：同上，把 --image 换成 --t2i
```

提示词文件（`p_thin.txt`）内容：

```
the #cfe6f2 area is pale blue gradient harem trousers
the #1b2a5c area is long navy gauze drape hanging from the belt down the side
the #d9c07a area is shoulder-length gold hair
the #0a0823 area is navy gauze belt tail
the #6f5fb0 area is small violet-and-blue lattice bodice panel
```

## 用法建议

想让参考**在某处起作用**，就不要把那一处写死：

| 想要的效果 | 提示词怎么写 |
|---|---|
| 参考决定这一块的细节 | 只写**品类 + 主色**，留白细节（`pale blue gradient harem trousers`） |
| 完全按自己的描述画 | 把细节写全（含材质、装饰、收口）——此时参考在这块等于没用 |
| 完全不要参考介入 | 不挂参考，或把该区从色图里排除 |

## 边界

- 测的是 `--edit-format ai-toolkit`（参考编码成 vision token）。
  `Moss-Image-Edit` 通道是另一套机制（参考走 VAE latent），未在此结论覆盖范围内。
- 单判据（腿部网格）单特征，未覆盖"配色精确度""图案拓扑"这类更细的维度。
- 3 seed 只能说明方向稳定，不能给出概率。
