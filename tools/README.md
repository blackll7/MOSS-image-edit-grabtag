# tools/ — SOP 配套脚本

SOP 17（[`../docs/sop-v2.md`](../docs/sop-v2.md)）的 S0 / S1 / 收尾三步所需的小脚本。
它们只管**目录与素材**，不碰模型；生成那一步仍由 `moss_run.py` 调 ComfyUI。

| 脚本 | SOP 步骤 | 作用 | 产物 |
|---|---|---|---|
| `run_new.py` | S0 | 建一个单列 run 目录（一次生成 = 一个目录） | `<runs>/<yyyyMMdd-HHmm>-<tag>/` + `run.json` + 两张源图副本 |
| `prep_canvas.py` | S1 | 参考裁 alpha + 合白底；画布高取参考、比例取深度；深度原样 | `ref_norm.png` / `depth_raw.png` / `prep.json` |
| `run_collect.py` | S3 后 | 把 `output-moss` 里的成品收进 `out/`，顺带写 `cmd.txt`、跑自检 | `out/*.png` / `audit/` |
| `selfcheck.py` | 自检 | 尺寸、留白、锐度、手数（手数需要 `hand_count.py`，缺了自动跳过） | `audit/{metrics.json,selfcheck.txt}` |

## 环境变量

本机路径不写死在代码里，换机器只需设两个变量：

```powershell
$env:MOSS_HOME = 'C:\MOSS-Deploy'   # ComfyUI 部署根（默认就是这个）
$env:MOSS_RUNS = 'D:\DSH-moss\runs' # run 目录根（默认 <当前目录>\runs）
```

## 用法

```powershell
$py = 'C:\MOSS-Deploy\venv\Scripts\python.exe'

# S0：建目录
$rd = (& $py tools\run_new.py --tag ice-s2 | Select-Object -Last 1)

# 把原图拷进去
Copy-Item 参考.png "$rd\01-ref-src.png"
Copy-Item 深度.png "$rd\02-depth-src.png"

# S1：归一
& $py tools\prep_canvas.py --ref "$rd\01-ref-src.png" --depth "$rd\02-depth-src.png" --outdir "$rd"

# ... S1.5 GrabTag / S2 提示词 / S3 moss_run.py 出图 ...

# 收成品 + 自检
& $py tools\run_collect.py --run "$rd" --prefix <moss_run 的 --prefix>
```

## 依赖

- Python 3.10+、Pillow、numpy；`selfcheck.py` 另需 OpenCV（`cv2`）算锐度。
- **不依赖** ComfyUI 的 Python 环境——用任何装了 Pillow 的解释器都能跑 S0/S1。
- `--depth-map` 的路径要绝对路径；`--color-map` 只认 `ComfyUI/input/` 下的文件名——这两条是 `moss_run.py` 的限制，不是本目录脚本的。
