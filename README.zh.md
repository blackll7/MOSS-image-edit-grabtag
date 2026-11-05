# GrabTag · 参考图分区打标

把"一张参考图丢给模型、祈祷它记住细节"换成**先把参考图切成有名字的物件、每件写一条 caption、导出 `layout.json`**——
模型读到的是"这条描述属于这个框"，而不是"这张图里某处有一朵莲花"。

仓库里同时带上配套的**色图工具**：横带／矩形／深度切片三种画法，加一个绿幕抠像器。

不带模型权重、不带素材、不绑定框架：纯 Python + Pillow，OpenCV / SAM 为可选。

## 快速上手

```bash
pip install -r requirements.txt
python examples/make_demo.py examples/demo.png
python grabtag.py plan --ref examples/demo.png --spec examples/demo_spec.json --out build/demo
#  看 build/demo/cm.png 与 montage.png，然后在 build/demo/regions.json 里填 caption
python grabtag.py build --dir build/demo
```

产物：`layout.json`（label / caption / bbox）+ `layout.audit.json`（每区覆盖体检）。

## 分区怎么定

| 参数 | 含义 |
|---|---|
| `--bands N` | 按人像高度切 N 段（最快） |
| `--band a:b --band c:d` | 自定义分数带 |
| `--rect name=x0,y0,x1,y1` | 自由框 |
| `--spec FILE` | JSON 清单：`name / rect / color? / tag? / caption? / seed? / priority? / object? / aliases? / requires? / no_clamp? / sam?` |

掩码可按区细化：`--masker grabcut`（默认，需 opencv）、`--masker sam-auto`（需 torch + segment-anything + `GRABTAG_SAM_CKPT`）、`--fit bg` 限制在人物轮廓内。

## 六条实测规矩

1. **一区一物件**；漏写的部件模型会自己补。
2. **颜色必须唯一**（导出侧按颜色归并，撞色 = 并成一件）。
3. **caption 必须写落点**：`a lotus ornament` 会跑到头发上，`… printed flat on the chest` 不会。
4. **12 区是甜点**，超过 16 区小件开始丢。
5. **窄框不会把人裂成几个，横跨整幅会**；band 模式 `--x-inset` 取 0.08–0.12。
6. **色图管几何，caption 管材质**；改 caption 不改轮廓，要改轮廓就重画色图。

## 环境变量

| 变量 | 用途 |
|---|---|
| `GRABTAG_SAM_CKPT` | SAM 权重路径（`sam_vit_b_*.pth`） |
| `GRABTAG_INPUT_DIR` | 色图同步目录，默认 `./ComfyUI/input`，不存在就跳过 |

MIT 许可，见 `LICENSE`；第三方说明见 `NOTICE.md`。
