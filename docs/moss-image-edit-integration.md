# Moss-Image-Edit × GrabTag 说明

> 版本：2026-11-08 · 依据本机实测（8 GB / RTX 4060 Laptop / ComfyUI 0.43.0 / 端口 8190）
> 本文只写**已验证**的东西；未验证的单独列在 §8。

---

## 1. 这套组合解决什么

**问题**：用参考图生成时，既要"服装纹样像参考"，又要"没提到的那件不出现"。这两件事在 MOSS 的两条参考通道上表现完全相反。

**结论**：用 `Moss-Image-Edit` 通道（参考走 VAE latent）+ 画布对齐补丁，才能两者兼得；GrabTag 负责"这块是什么、允不允许引用"。

---

## 2. 两条参考通道的本质差异（先看这张表）

| | `ai-toolkit`（vision tokens） | `Moss-Image-Edit`（VAE latent） |
|---|---|---|
| 参考去哪 | 编码成**文本 token**，等于"参考变成提示词的一部分" | 作为 **latent KV** 进注意力 |
| 空提示词时 | **整套参考装自动出现** | **参考几乎不生效** |
| 选择性 | ❌ 要就全要 | ✅ **提示词 = 检索键，提到才取** |
| 纹样保真 | 弱（~1–3 千 token 的语义摘要） | **强（像素级）** |
| token 成本 | 大（0.44 MiB/token 的 KV 缓存） | 小（同尺寸下与输出同量级） |

**实测对照**：同一张参考、同一句 `only one person in the frame`
- `ai-toolkit`：蓝紫宝石胸衣 + 蓝金腰饰 + 纱袖，整套复现；
- `Moss-Image-Edit`：黑发红黑紧身衣 + 牛仔裤，**与参考无关**。

→ "未被提到的 tag 不产生影响"这个目标，**只能在 `Moss-Image-Edit` 实现**。

---

## 3. 前置条件（少一个就错）

1. **`--edit-format Moss-Image-Edit`** —— 参考走 VAE latent 才有 ref 序列可对齐。
2. **画布对齐补丁** `MOSS_REF_ALIGN=1`
   `comfy/ldm/moss_image/model.py:246`，把参考的 rope 网格从"它自己的 H×W"改成"输出的 H×W"：
   ```python
   [ref.shape[-2:] for ref in refs]
   → ([(H, W)] * len(refs)) if os.environ.get("MOSS_REF_ALIGN")=="1" else [ref.shape[-2:] for ref in refs]
   ```
   备份：`model.py.bak_refpool`。改完重启 8190。
3. **输出分辨率 = 参考分辨率**（例：参考短边 1028 → 输出 1028×1821）。不同尺寸时"共享坐标系"没有意义。
4. **参考图 alpha 必须合成到白底**
   ```python
   im = Image.open(p).convert("RGBA")
   Image.alpha_composite(Image.new("RGBA", im.size, (255,255,255,255)), im).convert("RGB")
   ```
   直接 `convert("RGB")` 会把透明读成**黑色** → 出图带黑框。
5. **压缩用 NEAREST**（舍弃像素），不要 `area`/`bilinear`（混合插值会把纹样糊掉）。
6. **必须给深度图**（`--depth-map`），否则构图直接抄参考。

---

## 4. 两段流水线

### 第一段：出底图（全局外观）

```powershell
$py = 'C:\MOSS-Deploy\venv\Scripts\python.exe'
# 先设环境变量再启动 8190（一次即可）
$env:MOSS_REF_ALIGN = '1'
$env:MOSS_REF_POOL  = '0'     # 同尺寸时不必池化；参考远大于画布时才需要

& $py C:\MOSS-Deploy\moss_run.py --port 8190 `
  --edit-format Moss-Image-Edit `
  --image  ref_s2_1028w.png:raw `
  --ref-max-edge 1028 `
  --ref-bias 1.5 `
  --depth-map  pose_p007_depth.png --control-strength 0.5 `
  --w 1028 --h 1821 --seed 4242 `
  --prompt "a blonde woman standing, ornate blue silk outfit with purple-white diamond patterns and gold filigree" `
  --prefix stage1
```

**`--control-strength` 决定布料松紧**（不影响纹样）：

| 值 | 服装形态 | 纹样 |
|---|---|---|
| 1.0 | 贴身材质的连体衣（宽松布被压扁） | 菱格 + 金蔓在 |
| **0.5** | 宽松长袍 + 垂坠下摆（推荐） | 菱格 + 金蔓 + 宝石在 |
| 0.3 | 更松，姿态开始可能飘 | 待验证 |

### 第二段：按 tag 局部精修

```powershell
& $py C:\MOSS-Deploy\moss_run.py --port 8190 `
  --region-edit  `
  --region-base <第一段底图> `
  --region-color <该 tag 的 rect 掩码，纯 0/255> `
  --region-denoise 0.60 `
  --image <该 tag 的裁片>:raw `
  --w 1028 --h 1821 --seed 4242 `
  --prompt "<该 tag 的 ref_tag 表述>" `
  --prefix stage2_bodice
```
**实测参照**：区内 mean|Δ| 30.0 / 区外 1.5（**20×** 局部度）——目前唯一证明确实"只改一块"的机制。

---

## 5. GrabTag 两部分分别起什么作用

| 产物 | 作用 | 用在哪一段 |
|---|---|---|
| `tags.json` → `ref_tag` | **检索键 / 目标声明**：这块该是什么 | 第一段写进提示词；第二段作为精修提示词 |
| `tags.json` → `crop` | 该区的像素真值 | **只**用于第二段 `--image`，不进全局 |
| `tags.json` → `rect`/`color` | 作用域 | 第二段转成 0/255 掩码 |
| `active --prompt` 命中集合 | **权限表**：决定哪些 tag 参与 | 未命中 → 不进第二段队列，裁片连传都不传 |

一句话：**tag 说"是什么"，active 说"允不允许"，裁片说"长什么样"，掩码说"落在哪"。**

---

## 6. 旋钮总表

| 旋钮 | 取值 | 管什么 |
|---|---|---|
| `MOSS_REF_ALIGN` | 0 / 1 | 参考 token 是否与画布共享坐标系（B 通道总开关） |
| `MOSS_REF_POOL` | 0 / 2 / 3 | 参考 KV 池化倍数（仅当参考远大于画布、ai-toolkit 下防 OOM 时用） |
| `--edit-format` | `Moss-Image-Edit` | 参考走像素（B 通道必须） |
| `--control-strength` | 0.3–1.0 | 布料贴身↔宽松；1.0 压扁宽松布 |
| `--ref-bias` | 0.5–2.0 | 抄得多狠；过高 → 退化复制、画面发糊 |
| `--region-denoise` | 0.55–0.65 | 局部重采强度；>0.75 会失去锚点 |
| 提示词 | — | **检索键**；笼统词会检索出"原型"（长袍），要写结构与纹样 |

---

## 7. 已知限制

1. **8 GB 显存上限 ≈ 2.9 MP**（1280×2272 可跑 5–6 min；1536×2720 失败、15 min 无结果）。要更大尺寸，先出 1280×2272 再用放大模型。
2. **`ai-toolkit` 的参考 KV 成本** = `28 × 2 × n_ref × 4096 × 2B ≈ 0.44 MiB/token`；16320 tok = 7.50 GiB（必爆）。全尺寸参考必须先 `MOSS_REF_POOL=2`。
3. **参考图带边框/背景时会被一起抄**（即使 alpha 已合成白底）：应把人物裁到铺满画布。

---

## 8. 未验证（下一步）

| 项 | 判据 |
|---|---|
| "结构 + 纹样"提示词能否精确继承 | 出图同时满足"露腰两件 + 菱格正确" |
| `--control-strength 0.3` 的姿态稳定性 | 与深度图对得上 |
| 第二段 `--region-edit` 与 rect 掩码形状是否吻合 | 只有该区变化、边缘无接缝 |
| 两段之间的画风/光照一致性 | 精修区与整体无色差 |
| 提示词笼统会"原型化" | 同一参考下，写 "ornate silk outfit" 出长袍、写 "crop top + harem pants" 出分体结构 |

---

## 9. 相关文件

| 文件 | 内容 |
|---|---|
| `C:\MOSS-Deploy\moss_run.py` | 生成入口（`--edit-format` / `--region-edit` / `--ref-bias` 等） |
| `C:\MOSS-Deploy\ComfyUI\comfy\ldm\moss_image\model.py:246` | B 通道画布对齐补丁 |
| `C:\MOSS-Deploy\ComfyUI\comfy\model_base.py:2945` | `ref_kv` 预计算（`MOSS_REF_POOL` 的落点） |
| `C:\MOSS-Deploy\moss_region_caption.py` | GrabTag 主程序（`plan` / `build` / `active`） |
| `D:\DSH-moss\经验-参考图token与OOM边界-20261108.md` | 全部实测记录与失败教训 |
| `D:\DSH-moss\工作目标.md` | 目标与验收标准 |

---

## 参考图的两个副作用：虚影与细节悖论（2026-11-08 · 五臂实测）

同一参考（`ref_s2_full.png` 1538×2724）、同一深度（`moss_dep_sx08.png` 960×1280）、
同一提示词（`case-v2/prompt.txt`，1681 字符、11 块全覆盖）、seed 4242、画布 1216×1632、
MiE 通道 + `MOSS_REF_POOL=2` + `MOSS_REF_ALIGN=1`、24 步。

| 臂 | 差异变量 | 腰部灰雾 | 锐度 | 饱和 | 颜色数 |
|---|---|---|---|---|---|
| A | `--layout` | 有（并伴单腿崩坏） | — | — | — |
| B | `--color-map` | **有** | 424 | 17.7 | 5934 |
| C | 纯文本（挂参考） | **有** | 414 | 18.4 | 5743 |
| D | 纯文本 + 去掉 `--ref-max-edge`（参考与输出**同尺寸**） | **有，更清楚** | 392 | 14.8 | 5371 |
| E | **不挂参考** | **无** | **445** | 16.9 | **6515** |

### 1. 虚影不是分辨率不一致造成的

D 臂里参考与画布一个像素都不差，灰雾照旧；E 臂一去掉参考就彻底消失 → 成因在参考通路本身。

### 2. 成因：对齐（把参考钉到画布坐标）× 池化（只留平均色）

- `comfy/ldm/moss_image/model.py:246`：`MOSS_REF_ALIGN=1` 时每张参考的 RoPE 坐标被设成画布 `(H,W)`
  → 画布上"腰部那块"的 query 会强烈 attend 到参考图上同一位置的 key。
- `model.py:227-236`：`MOSS_REF_POOL=2` 用 `avg_pool2d` 把参考 latent 做 2×2 平均
  → 薄纱、冰晶这类低对比细节被抹成一片均值，结构消失。
- 两者相乘：位置被钉住 + 内容只剩均值 → 该区域输出成一块**去色半透明雾**。

数值：灰雾区与参考同区比较 —— 彩度 **16.5 vs 48.7**（去色），亮度 **149.9 vs 110.6**（被拉亮）；
而"模拟 pool2 后的参考"彩度 42.6，也不等于灰雾 → 它不是抄参考，是**被参考拉向平均值**。

### 3. 细节悖论：prompt 写全时，参考反而降低细节

锐度 445（无参考）> 424（color-map）> 414（纯文本 + 参考）> 392（同尺寸参考）；
颜色多样性 6515 > 5934 > 5743 > 5371 —— **单调下降，参考介入越深越低**。

原因：参考 token 与图像 token 争注意力预算，而它提供的又只是低频均值，
净效果是压低了局部对比而不是补细节。

**判据：参考图的价值 = prompt 的信息缺口。**
- prompt 已按 11 块写全（形状/材质/颜色/位置）→ 参考是噪声源，**别挂**；
- prompt 写不出特定花纹或款式（"这件立领上的缠枝金线"）→ 参考是必需，接受雾或按下面降级。

### 4. 需要参考时的四种降级（按成本）

| # | 做法 | 代价 |
|---|---|---|
| 1 | `MOSS_REF_ALIGN=0`（参考不再钉到画布坐标） | 需重启服务端；参考变"风格参考"而非"位置参考" |
| 2 | `MOSS_REF_POOL=0`（保留高频） | ref_kv 从 0.83 → 3.31 GiB，8 GB 有 OOM 风险 |
| 3 | 只挂裁片 `gt/crops/*.png`，不挂整图 | 小件无大面积低对比纱，token 少而精 |
| 4 | 提示词对该区域给出更明确的描述，压过参考 | 需要先知道是哪个区在起雾 |
