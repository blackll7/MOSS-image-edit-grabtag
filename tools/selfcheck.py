# -*- coding: utf-8 -*-
"""selfcheck.py — 出图后自检：审图表 + 手数，一条命令。
用法： python selfcheck.py <渲染图> [--cell 430]
产出： C:/MOSS-Deploy/ComfyUI/input/_audit_sheet.png（6 块特写）
      标准输出最后三行：审图表路径 / 手数结论 / 一句话判读
注意：mediapipe 对手绘/动漫图的手部检出率低且有误报（v376 双手叉腰只检出 1 只，
      v377 实为 2 只却报 3 只），**手数只作参考信号，最终以审图表人眼确认为准**。
"""
# 路径来自环境变量，便于换机器：
#   MOSS_HOME  ComfyUI 部署根（默认 C:\MOSS-Deploy）
#   MOSS_RUNS  run 目录根（默认 ./runs）
import os as _os
MOSS_HOME = _os.environ.get("MOSS_HOME", r"C:\MOSS-Deploy")
MOSS_RUNS = _os.environ.get("MOSS_RUNS", _os.path.join(_os.getcwd(), "runs"))
import io,sys,os,subprocess,json,argparse
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding="utf-8",errors="replace")
import numpy as np, cv2

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("image"); ap.add_argument("--cell", type=int, default=430)
    a=ap.parse_args()
    im=cv2.imdecode(np.fromfile(a.image,np.uint8),cv2.IMREAD_COLOR)
    if im is None: print("!! 读不到",a.image); sys.exit(2)
    H,W=im.shape[:2]; g=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    ys,xs=np.where(g<245)
    x0,x1,y0,y1=int(xs.min()),int(xs.max()),int(ys.min()),int(ys.max()); fw,fh=x1-x0,y1-y0
    def box(rx0,ry0,rx1,ry1):
        return (int(x0+rx0*fw), int(y0+ry0*fh), int(x0+rx1*fw), int(y0+ry1*fh))
    regions=[("face",box(0.28,0.02,0.72,0.16)),("chest+arms",box(0.10,0.16,0.90,0.40)),
             ("hand-left",box(0.00,0.20,0.42,0.52)),("hand-right",box(0.58,0.20,1.00,0.52)),
             ("hip+thigh",box(0.25,0.42,0.75,0.62)),("feet",box(0.20,0.86,0.80,1.00))]
    tiles=[]
    for name,(p,q,r,s) in regions:
        cr=im[max(0,q):s, max(0,p):r]
        if cr.size==0: continue
        k=a.cell/max(cr.shape[:2])
        tiles.append((name,cv2.resize(cr,(max(1,int(cr.shape[1]*k)),max(1,int(cr.shape[0]*k))),interpolation=cv2.INTER_LANCZOS4)))
    cols=3; rows=(len(tiles)+cols-1)//cols
    o=np.full((rows*(a.cell+26)+8, cols*(a.cell+8)+8,3),255,np.uint8)
    for i,(name,t) in enumerate(tiles):
        r,c=divmod(i,cols); y=8+r*(a.cell+26); x=8+c*(a.cell+8)
        o[y:y+t.shape[0], x:x+t.shape[1]]=t
        cv2.putText(o,name,(x+4,y-6),cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,0,0),1)
    sheet=os.path.join(MOSS_HOME, "ComfyUI", "input", "_audit_sheet.png")
    cv2.imencode(".png",o)[1].tofile(sheet)
    print("审图表:",sheet)
    # 手数（参考信号）
    hp=os.path.join(MOSS_HOME, "work", "hand_count.py")
    r2=subprocess.run([sys.executable,hp,a.image,"--json"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    try:
        d=json.loads((r2.stdout or "").strip().split("\n")[-1]); n=d["hands"]
    except Exception:
        n=-1
    print("手数(参考):", n if n>=0 else "检测失败")
    print("判读: 请打开审图表逐块看 —— 手指数/多肢/断肢/脚数/%s"%(("⚠ 手数报警→%d 只，重点看 hand-left / hand-right"%n) if n>2 else "手数正常"))
if __name__=="__main__": main()