# -*- coding: utf-8 -*-
"""run_new.py —— 为「一次生成」建一个单列文件夹（SOP-v2 第 0 步）

一次生成 = 一个 run 目录，改造后的素材、命令、提示词、清单、成品、自检结果**全部只进这一个目录**，
不与既有目录混放。

用法:
  python run_new.py --tag ice-s2 [--root D:\\DSH-moss\\runs] [--note "说明"]
  打印 run 目录绝对路径（最后一行），供后续脚本用。

目录结构（生成时逐步填充）:
  <root>/<yyyyMMdd-HHmm>-<tag>/
    run.json            元信息（时间/标签/说明/状态）
    01-ref-src.png      原始参考图（拷贝，便于回溯）
    02-depth-src.png    原始深度图（拷贝）
    ref_norm.png        第一步产物（<=2MP，NEAREST）
    depth_norm.png      第二步产物（与参考同高）
    prep.json           三步的尺寸/MP/token/ref_kv 数字
    prompt.txt          本次提示词全文
    cmd.txt             本次完整命令
    manifest.md         参数清单（write_manifest.py 写这里）
    out/                成品（collect 时从 output-moss 复制）
    audit/              审图表 / 手数 / 指标
"""
# 路径来自环境变量，便于换机器：
#   MOSS_HOME  ComfyUI 部署根（默认 C:\MOSS-Deploy）
#   MOSS_RUNS  run 目录根（默认 ./runs）
import os as _os
MOSS_HOME = _os.environ.get("MOSS_HOME", r"C:\MOSS-Deploy")
MOSS_RUNS = _os.environ.get("MOSS_RUNS", _os.path.join(_os.getcwd(), "runs"))
import argparse, json, os, shutil, sys, time

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="run")
    ap.add_argument("--root", default=MOSS_RUNS)
    ap.add_argument("--note", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--depth", default="")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M")
    tag = "".join(c if (c.isalnum() or c in "-_") else "-" for c in a.tag)
    rd = os.path.join(a.root, "%s-%s" % (stamp, tag))
    n = 2
    while os.path.exists(rd):
        rd = os.path.join(a.root, "%s-%s-%d" % (stamp, tag, n)); n += 1
    for sub in ("out", "audit"):
        os.makedirs(os.path.join(rd, sub), exist_ok=True)

    meta = {"run": os.path.basename(rd), "dir": rd, "tag": tag, "note": a.note,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "created",
            "ref_src": "", "depth_src": "", "canvas": None, "prompt_file": "", "prefix": ""}
    if a.ref and os.path.exists(a.ref):
        dst = os.path.join(rd, "01-ref-src" + os.path.splitext(a.ref)[1].lower())
        shutil.copy2(a.ref, dst); meta["ref_src"] = os.path.basename(dst)
    if a.depth and os.path.exists(a.depth):
        dst = os.path.join(rd, "02-depth-src" + os.path.splitext(a.depth)[1].lower())
        shutil.copy2(a.depth, dst); meta["depth_src"] = os.path.basename(dst)
    p = os.path.join(rd, "run.json")
    open(p, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    print("run 目录：", rd)
    print(rd)

if __name__ == "__main__":
    main()
