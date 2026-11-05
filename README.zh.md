# GrabTag

把一张参考图**切成有语义的区域**，再把这些区域写成**可以驱动扩散模型的分区条件**。
仓库里同时收了配套的**区域 ID 色图**工具（含绿幕抠像）。

不需要任何模型权重即可跑通全流程；SAM 只在你想用自动抠形时才是可选项。

![区域色图](docs/images/demo-cm.png)

*`plan` 在示例图上产出的色图：每一种纯色就是一个区域 ID；`montage.png` 里是同批区域带序号的裁片，逐块打标即可。*

---

## 两个工具集

| 工具 | 做什么 | 产出 |
|---|---|---|
| **GrabTag** | `plan` 切区 + 出裁片/色图/清单；`build` 出 `layout.json` 与覆盖体检 | `crops/` `montage.png` `cm.png` `regions.json` `tags.json` `layout.json` `layout.audit.json` |
| **色图工具** | `colormap`（横带 / PCA / 边缘）、`depth-colormap`（深度切片）、`chroma`（绿幕抠像） | 区域 ID 图 / RGBA |

## 安装

```bash
pip install -e .            # 核心（numpy + Pillow）
pip install -e ".[mask]"    # 加 GrabCut（opencv-python-headless + scipy）
pip install -e ".[sam]"     # 再加 SAM 自动抠形（torch + segment-anything）
```

验证安装：

```bash
grabtag --help
```

## 三分钟上手

```bash
# 1) 生成一张版权自有的示例图（不用任何外部素材）
python examples/make_demo.py build/demo.png

# 2) 切区：给一份 spec，或直接用 --bands 4 自动横切
grabtag plan --ref build/demo.png --spec examples/demo_spec.json --out build/reg

# 3) 看 build/reg/montage.png，把每一格的 caption 填进 regions.json

# 4) 导出分区条件 + 覆盖体检
grabtag build --dir build/reg
```

`layout.json` 里每项是 `{label, caption, bbox}`，`bbox` 是 **yxyx 像素、以画布中心为原点**（例如 `[-52,-154,133,153]`）。
给人看的审计信息（`kept`、`loss_hi`、越界告警）单独写进 `layout.audit.json` —— 不要把它们塞进 `layout.json`，那个文件会被下游当提示词读进去。

## 为什么要有 `plan`

手写坐标容易把整块布当成一个区域，最后模型把一块布读成一个人。`plan` 负责的是**先把区域画出来给自己看**：

- `--bands N`：等分横切（最快，适合从头到脚的衣服）
- `--band a:b`：自己指定切点（例如避开腰线）
- `--rect` / `--spec`：矩形或 JSON 声明式清单，支持 `priority`、`seed`、`aliases`、`object`
- 掩码细化：`--masker grabcut`（默认）/ `sam-auto` / `sam-bp` / `sam-box` / `sam-point`
- 覆盖体检：`kept` 低说明这个区被更大的区吃掉了，`loss_hi` 说明吃掉它的是同级或更低优先级的区

## 环境变量

| 变量 | 作用 |
|---|---|
| `GRABTAG_SAM_CKPT` | SAM 权重路径（默认当前目录下的 `sam_vit_b_01ec64.pth`） |
| `GRABTAG_INPUT_DIR` | 若设置，`plan` 会把 `cm.png` 同步过去（例如 ComfyUI 的 `input/`）；留空则不同步 |

## 与 ComfyUI 的关系

这套工具**不依赖 ComfyUI**，只负责产出文件。若你在 ComfyUI 里用 MOSS / Qwen-Image 之类的分区条件：

- 色图要放在 `ComfyUI/input/` 下、以**相对文件名**传给 `--color-map`
- 挂色图时画布像素数建议 ≤ 4096 个 16×16 块（`(w/16)*(h/16) ≤ 4096`）
- `--layout` 这类"把 bbox 变成 prompt"的做法属于下游实现，本仓库不做

## 许可

MIT。示例图由脚本生成，仓库内不含任何第三方素材与模型权重。
