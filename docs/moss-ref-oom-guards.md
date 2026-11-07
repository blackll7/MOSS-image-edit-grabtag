# MOSS 参考图显存防线（OOM guards）

> 适用版本：ComfyUI 0.43.0 + MOSS-Image（`moss_image` 系列权重）+ 本仓库的 `ref-bias` 自定义节点集。
> 场景：使用 **1024² 以上的参考图**、或**参考图与输出同尺寸**（`Moss-Image-Edit` 通道）时的显存爆炸。

---

## 1. 现象

KSampler 报：

```
torch.OutOfMemoryError: Allocation on device 0 would exceed allowed memory.
Currently allocated: 7.99 GiB, Requested: 7.84 GiB, Device limit: 8.00 GiB
```

特征：**与采样步数、cfg、LoRA 无关**；只和「参考图 token 数」与「画布面积」同时相关。缩小参考图能绕过，但代价是细节丢失。

---

## 2. 根因

不是 attention 工作区，而是 **reference KV 被一次性物化成「全层缓存」**。

`comfy/ldm/moss_image/model.py` → `preprocess_refs()`：

```python
kv = x.new_empty((x.shape[0], len(self.layers), 2) + x.shape[1:])
```

即：

```
28 层 × 2(K/V) × n_ref × 4096 dim × 2 B(fp16)  ≈  0.44 MiB / token
```

| n_ref（参考 token 数） | KV 缓存 |
|---|---|
| 1792 | 0.79 GiB |
| 3213 | 1.47 GiB |
| 7232 | 3.2 GiB |
| 16320 | **7.5 GiB** |

最后一行正好对上 `Requested 7.84 GiB`——这就是 8 GB 卡「1024² 以上参考必爆」的全部原因。

---

## 3. 三道防线

### 防线 1 · 参考 latent 池化（主力）

```bash
# 必须在启动 ComfyUI 之前设置，且是【服务进程】的环境变量
set MOSS_REF_POOL=2
start-comfyui.bat
```

- 落点：`comfy/model_base.py` 的 `extra_conds()` 里、`preprocess_refs()` **之前**。
- 做法：先对 `ref_latents` 做 2×2 平均池化，`n_ref ÷ 4`，KV 缓存同比缩小。
- **编码阶段仍是全分辨率**，高频细节保留；只有 KV 这一步降采样。
- 日志：`[moss-ref-pool] 113x64 -> 56x32 (7232 -> 1792 tok)`

> 注意：设在调用端的 shell（如 `$env:MOSS_REF_POOL=2; python moss_run.py`）**无效**，因为池化发生在服务进程内。

### 防线 2 · 关闭预计算（排查用）

```bash
set MOSS_NO_REF_PRECOMPUTE=1
```

- 跳过 `out['ref_kv'] = ... preprocess_refs(...)`，改为把 `ref_latents` 透传给 `_forward`，每步现算。
- 日志：`[moss-ref] precompute OFF (MOSS_NO_REF_PRECOMPUTE=1) -> ref_latents passthrough`
- **定位说明**：峰值同样取决于 `n_ref`，因此它**不能替代池化**；只用于确认「OOM 是否由预计算这一句引起」。默认关闭。

### 防线 3 · DyPE 默认关闭，开启时按原生比例外推

在老实现里，画布面积 > `base²` 会自动启用 DyPE-M，并用固定的 `target_scale = 1.25` **凭空放大画布**——1.86 MP 就需要额外 3.5 GiB，直接爆。

新行为：

| 调用 | 行为 | 日志 |
|---|---|---|
| 默认（不带 `--dype`） | 关闭 | `DyPE-M off (--dype not set; area=1.86MP)` |
| 面积不足 | 关闭 | `DyPE-M off (area=0.79MP <= base 1024^2=1.05MP)` |
| 显式 `--dype` | **按设置分辨率的原生比例外推**（`target_scale=0` → 用 `base_resolution`） | `DyPE-M ON  area=1.86MP  base=1024  latent=64x113  extrapolate=1.00x1.77` |

`extrapolate` 显示的是宽/高两个方向各自的实际倍数（`1024×1814` → latent `64×113`，基准 `64×64`），不再是那个容易被误读的单一 `target_scale`。

---

## 4. 一个必须记住的位置

> **所有池化/开关都必须落在 `comfy/model_base.py` 的 `extra_conds()` 中、`preprocess_refs()` 调用之前。**

改 `comfy/ldm/moss_image/model.py` 内部**无效**：在 `ai-toolkit` / `Moss-Image-Edit` 路径下，`ref_kv` 是上游预计算好再传下来的，`_forward` 里的 `ref_latents` 分支进不去。这一点曾让四次补丁全部落空。

---

## 5. 实测配方（B 通道：参考与输出同尺寸）

```bash
# 服务端
set MOSS_REF_POOL=2
set MOSS_REF_ALIGN=1

# 调用端
python moss_run.py ^
  --image ref_s2_1024w.png:raw --ref-max-edge 1814 ^
  --edit-format Moss-Image-Edit ^
  --depth-map dep_1024.png --control-strength 0.75 ^
  --w 1024 --h 1814 --no-dype ^
  --no-fast --steps 24 --cfg 3.5 --scheduler sgm_uniform ^
  --prompt-file p_s2_1024b.txt
```

- 参考：与输出同尺寸（1024×1814，7232 tok）
- 输出：1024×1814（16 对齐后 1024×1808）
- 结果：姿态、服装结构、纹样三者同时对齐

### `--control-strength` 三档对照

| 值 | 效果 |
|---|---|
| 1.0 | 深度图把布料压到皮肤上 → 贴身连体衣 |
| **0.75** | 布料蓬松、姿态仍贴合（推荐） |
| 0.5 | 布料最松，但姿态容易跟不上深度图 |

---

## 6. 8 GB 显存的分辨率天花板（实测）

| 画布 | 面积 | 结果 |
|---|---|---|
| 768×1024 | 0.79 MP | ✅ ~50 s |
| 1024×1808 | 1.85 MP | ✅ ~90 s（池化 + `--no-dype`） |
| 1280×2272 | 2.91 MP | ✅ 5–6 min |
| 1536×2720 | 4.18 MP | ❌ 15 min 无结果（判定不可用） |

上限约 **2.9 MP**。需要更大尺寸时，先出 2.9 MP 再用外部放大模型，而不是直接抬高采样分辨率。

---

## 7. 相关开关速查

| 开关 | 位置 | 默认 | 作用 |
|---|---|---|---|
| `MOSS_REF_POOL` | 服务端 env | `0`（关） | `2` → 参考 KV ÷4 |
| `MOSS_NO_REF_PRECOMPUTE` | 服务端 env | `0`（关） | `1` → 跳过预计算（排查用） |
| `MOSS_REF_ALIGN` | 服务端 env | `0`（关） | `1` → 参考 token 与画布共享坐标系（`Moss-Image-Edit` 下让纹样对齐） |
| `--dype` / `--no-dype` | CLI | 关 | 大画布位置外推 |
| `--ref-bias` | CLI | `0` | 参考权重（过高会退化成涂抹） |
| `--ref-max-edge` | CLI | `768` | 参考长边；**与显存线性相关** |
| `--control-strength` | CLI | `1.0` | 布料松紧（0.75 推荐） |
