# -*- coding: utf-8 -*-
"""run_collect.py —— 把一次生成的成品收进 run 目录，并就地出指标与审图表（SOP-v2 收尾步）

用法:
  python run_collect.py --run-dir <run目录> --prefix <moss_run 的 --prefix> [--audit]
  从 C:\\MOSS-Deploy\\ComfyUI\\output-moss\\<prefix>*.png 复制到 <run>/out/，
  写 <run>/audit/metrics.json（占高/占宽/比例/饱和/锐度/肤色/深色），
  若 --audit 则调 selfcheck.py 并把审图表另存到 <run>/audit/_audit_sheet.png，
  最后把 run.json 的 status 改成 collected、列出成品文件名。

不改动 ComfyUI 里的原图，只复制。
"""
# 路径来自环境变量，便于换机器：
#   MOSS_HOME  ComfyUI 部署根（默认 C:\MOSS-Deploy）
#   MOSS_RUNS  run 目录根（默认 ./runs）
import os as _os
MOSS_HOME = _os.environ.get("MOSS_HOME", r"C:\MOSS-Deploy")
MOSS_RUNS = _os.environ.get("MOSS_RUNS", _os.path.join(_os.getcwd(), "runs"))
import argparse, glob, json, os, shutil, subprocess, sys
import numpy as np, cv2

def metrics(im):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = g < 245
    ys, xs = np.where(m)
    H, W = g.shape
    skin = (((hsv[..., 0] < 25) | (hsv[..., 0] > 165)) & (hsv[..., 1] > 40) & (hsv[..., 2] > 110) & m).mean() * 100
    dark = ((hsv[..., 1] > 60) & (hsv[..., 2] < 120) & m).mean() * 100
    return {"size": [W, H], "h_ratio_pct": round((ys.max() - ys.min()) / H * 100, 1),
            "w_ratio_pct": round((xs.max() - xs.min()) / W * 100, 1),
            "aspect_wh": round(W / H, 3), "sat_mean": round(float(hsv[..., 1][m].mean()), 1),
            "sharp_lap": int(cv2.Laplacian(g, cv2.CV_64F).var()),
            "skin_pct": round(float(skin), 2), "dark_pct": round(float(dark), 2)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--src", default=os.path.join(MOSS_HOME, "ComfyUI", "output-moss"))
    a = ap.parse_args()
    os.makedirs(os.path.join(a.run_dir, "out"), exist_ok=True)
    os.makedirs(os.path.join(a.run_dir, "audit"), exist_ok=True)
    files = sorted(glob.glob(os.path.join(a.src, a.prefix + "*.png")))
    if not files:
        print("!! 没找到成品：%s\\%s*.png" % (a.src, a.prefix)); sys.exit(2)
    out, res = [], {}
    for f in files:
        dst = os.path.join(a.run_dir, "out", os.path.basename(f))
        shutil.copy2(f, dst)
        im = cv2.imdecode(np.fromfile(dst, np.uint8), cv2.IMREAD_COLOR)
        res[os.path.basename(f)] = metrics(im)
        out.append(os.path.basename(dst))
    open(os.path.join(a.run_dir, "audit", "metrics.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    if a.audit:
        first = os.path.join(a.run_dir, "out", out[0])
        sc = os.path.join(MOSS_HOME, "work", "selfcheck.py")
        r = subprocess.run([sys.executable, sc, first], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        sheet = os.path.join(MOSS_HOME, "ComfyUI", "input", "_audit_sheet.png")
        if os.path.exists(sheet):
            shutil.copy2(sheet, os.path.join(a.run_dir, "audit", "_audit_sheet.png"))
        open(os.path.join(a.run_dir, "audit", "selfcheck.txt"), "w", encoding="utf-8").write(
            (r.stdout or "") + (r.stderr or ""))
        print((r.stdout or "").strip())
    # 更新 run.json
    jp = os.path.join(a.run_dir, "run.json")
    if os.path.exists(jp):
        meta = json.load(open(jp, encoding="utf-8"))
        meta["status"] = "collected"; meta["prefix"] = a.prefix; meta["outputs"] = out
        meta["metrics"] = res
        open(jp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    sys.stdout.reconfigure(encoding="utf-8")
    print("成品 %d 张 → %s" % (len(out), os.path.join(a.run_dir, "out")))
    for k, v in res.items():
        print("  %-28s 占高%5.1f%% 占宽%5.1f%% 比例%.3f 饱和%5.1f 锐度%6d 肤色%5.2f%%"
              % (k, v["h_ratio_pct"], v["w_ratio_pct"], v["aspect_wh"], v["sat_mean"], v["sharp_lap"], v["skin_pct"]))

if __name__ == "__main__":
    main()
