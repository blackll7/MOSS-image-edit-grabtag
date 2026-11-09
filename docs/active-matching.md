# `active` 的两处硬化：泛词闸门 + 自动落点

`grabtag active` 负责一件事：**从提示词里判断"哪些区该生效"**，并据此输出颜色键行。

这个判断原来有两个毛病：

1. **匹配太松**——`sash_point` 的 `ref_tag` 是 `belt tail, sash tail`，但只要提示词里出现 `navy` 一个词就会命中；
2. **颜色键只有"是什么"没有"画在哪"**——`the #cfe6f2 area is <描述>`，位置得手写。

两处都已修。

## 一、泛词闸门

`GENERIC` 表只收**颜色 / 材质 / 纯装饰**：

| 类 | 例 |
|---|---|
| 颜色 | navy, blue, pale, light, dark, deep, white, black, grey, silver, gold, red, violet, purple, green, cyan, teal, pink, warm, cool, bright |
| 材质 | silk, satin, gauze, sheer, lace, mesh, crystal, glass, pearl, bead, beaded, metal, metallic, fabric, cloth |
| 纯装饰 | trim, ornament(s), chain(s), gem(s), jewel(s) |

规则：一条 `ref_tag` 短语拆成实词后，**若全部落在 `GENERIC` 里，直接判不命中**。

### 为什么部件名词不能进 GENERIC

第一版把 `hair / bodice / shoes / trousers / belt / sash / drape / veil / sleeve / orb / top / legs / skirt / hem / gloves / boots` 也放进了泛词表，结果 `bodice`（`ref_tag` 就是单词 `bodice`）**永远命中不了**——单词短语的实词集合等于它自己，被整条滤掉。

**部件名是定位词，不是泛词。** 提示词说 `bodice`，就是要那块胸衣，这正是目标要的语义。

### 验证

```powershell
python grabtag.py active --dir work\reg_ice3 --prompt "<下面每条>" --out x.json
```

| # | 提示词 | 预期 | 实测 |
|---|---|---|---|
| T1 | `a woman in a navy and silver outfit, gauze and lace, plain background` | 0 | **命中 0 / 未命中 11** |
| T2 | `a figure with silver trim and gold chains, plain background` | 0 | **命中 0 / 未命中 11** |
| T3 | `a girl with braids, wearing shoes and a bodice, standing` | 3 | **命中 3** |
| T4 | `a woman in pale blue harem pants` | 1 | **命中 1** |
| T5 | `a woman wearing a violet lattice bodice, pale blue harem pants, a navy drape over one shoulder, gold braids, a veil, shoes, a belt` | 7 | **命中 7 / 未命中 4** |

T1/T2 是这次的靶子：只提颜色和材质，一个区都不进。

## 二、自动落点

颜色键从

```
the <color> area is <ref_tag / paint_prompt>
```

改成

```
the <color> area, <vertical band>, <horizontal band>[, <size>], shows <ref_tag / paint_prompt>
```

### 垂直五档（按 `rect` 的 `cy`）

| cy | 文案 |
|---|---|
| < 0.20 | `the head and shoulders` |
| 0.20 – 0.36 | `the chest level, upper torso` |
| 0.36 – 0.50 | `the waist and hip level` |
| 0.50 – 0.75 | `the thighs and knees` |
| ≥ 0.75 | `the lower legs and feet` |

### 水平三档（按 `cx`）

`on the left side`（< 0.40）/ `in the centre` / `on the right side`（> 0.60）

### 面积

`> 0.16` 追加 `, a large area`；`< 0.04` 追加 `, a small area`。

### 验证（T5 的 7 条）

| 区 | 落点 |
|---|---|
| legs `#cfe6f2` | the thighs and knees, in the centre, a large area |
| drape `#1b2a5c` | the thighs and knees, on the left side |
| hair `#d9c07a` | the head and shoulders, in the centre |
| veil `#e8f2f8` | the chest level, upper torso, in the centre |
| shoes `#8fc7de` | the lower legs and feet, in the centre, a small area |
| sash `#2b3f7a` | the waist and hip level, in the centre, a small area |
| bodice `#6f5fb0` | the chest level, upper torso, in the centre, a small area |

7 条全部落在正确部位。

### 阈值是给人定的

`0.20 / 0.36 / 0.50 / 0.75` 这组分档是按 `reg_ice3` 的实际 `rect` 反推的（hair cy≈0.155、bodice≈0.245、sash≈0.36、legs≈0.677）。换一套参考图、换一种构图（半身 / 特写）需要重标。这是**位置先验**，不是几何真理。

## 三、端到端没被破坏

```powershell
python moss_run.py --port 8190 `
  --image 'v2sop_ref.png:raw' --color-map 'regcap_gt.png' --depth-map '<abs>\v2sop_depth.png' `
  --control-strength 0.75 --edit-format ai-toolkit --fast `
  --prompt-file '<基础句 + 7 条带落点颜色键>' --w 1216 --h 1632 --seed 9001 --prefix e2e_fast
```

`e2e_fast_00001_.png`（2,127,770 B），**33 s**，无异常。

> 注：该图腿部仍出现冰晶菱格，是因为 `regions.json` 里 `legs` 的 `paint_prompt` 还是旧的 `clear blue ice sheath...`。属数据待更新，与本补丁无关。

## 四、还有一个坑

`_phrase_hit` 有两条分支：

1. 整短语直接出现在提示词里 → 命中；
2. 短语内实词全部出现 → 命中。

泛词闸门最初只加在分支 2，于是 `hair`（`ref_tag` = `hair`）走分支 1 时**绕过了闸门**，提示词里出现 `hair` 就无条件拉入。已修：两条分支都要过闸门。
