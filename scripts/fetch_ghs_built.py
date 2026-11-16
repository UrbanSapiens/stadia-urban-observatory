#!/usr/bin/env python3
"""Download GHSL layers and crop source-grid-aligned rasters per venue."""
import argparse, csv, json, math, os, shutil, subprocess, time, zipfile
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.windows import Window

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(ROOT, "data", "index.json")
CACHE_DIR = os.path.join(ROOT, "cache", "ghs_built")
TILE_DIR = os.path.join(CACHE_DIR, "tiles")
POP_TILE_DIR = os.path.join(ROOT, "cache", "ghs_pop", "tiles")
DOCS_DIR = os.path.join(CACHE_DIR, "docs")
MANIFEST_PATH = os.path.join(ROOT, "outputs", "ghs_built_manifest.csv")
BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
LAYERS = {
 "anbh": {"folder":"GHS_BUILT_H_GLOBE_R2023A/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100/V1-0/tiles/","prefix":"GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0","cell":100,"half_box_m":10000,"mode":"tile_zip"},
 "agbh": {"folder":"GHS_BUILT_H_GLOBE_R2023A/GHS_BUILT_H_AGBH_E2018_GLOBE_R2023A_54009_100/V1-0/tiles/","prefix":"GHS_BUILT_H_AGBH_E2018_GLOBE_R2023A_54009_100_V1_0","cell":100,"half_box_m":10000,"mode":"tile_zip"},
 "vol": {"folder":"GHS_BUILT_V_GLOBE_R2023A/GHS_BUILT_V_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/","prefix":"GHS_BUILT_V_E2020_GLOBE_R2023A_54009_100_V1_0","cell":100,"half_box_m":10000,"mode":"tile_zip"},
 "fun": {"folder":"GHS_BUILT_C_GLOBE_R2023A/GHS_BUILT_C_FUN_E2018_GLOBE_R2023A_54009_10/V1-0/GHS_BUILT_C_FUN_E2018_GLOBE_R2023A_54009_10_V1_0/","prefix":"GHS_BUILT_C_FUN_E2018_GLOBE_R2023A_54009_10_V1_0","cell":10,"half_box_m":2000,"mode":"remote_tif"},
 "msz": {"folder":"GHS_BUILT_C_GLOBE_R2023A/GHS_BUILT_C_MSZ_E2018_GLOBE_R2023A_54009_10/V1-0/tiles/","prefix":"GHS_BUILT_C_MSZ_E2018_GLOBE_R2023A_54009_10_V1_0","cell":10,"half_box_m":2000,"mode":"extracted_tile"},
 "smod": {"folder":"GHS_SMOD_GLOBE_R2023A/GHS_SMOD_E2020_GLOBE_R2023A_54009_1000/V2-0/","prefix":"GHS_SMOD_E2020_GLOBE_R2023A_54009_1000_V2_0","cell":1000,"half_box_m":10000,"mode":"global_zip"},
 "pop": {"folder":"GHS_POP_GLOBE_R2023A/GHS_POP_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/","prefix":"GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0","cell":100,"half_box_m":10000,"mode":"tile_zip"},
}
DOC_LAYERS = {"anbh", "vol", "msz", "smod"}
DOWNLOAD_ATTEMPTS, DOWNLOAD_PAUSE_SECONDS = 6, 5
FUN_ATTEMPTS, FUN_PAUSE_SECONDS = 3, 5
# ----------------------------------------
TO_MOLL = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
FIELDS = ["slug","layer","tiles","width","height","dtype","nodata","nodata_cells","valid_cells","min","max","mean","n_distinct","status"]

class MissingTile(Exception): pass

def tile_for(x,y):
 return math.floor((9000000-y)/1000000)+1, math.floor((x+18041000)/1000000)+1

def needed_tiles(box):
 l,b,r,t=box; e=.001
 return sorted({tile_for(x,y) for x in (l,r-e) for y in (b+e,t)})

def tile_name(layer,row=None,col=None):
 p=LAYERS[layer]["prefix"]
 return p if LAYERS[layer]["mode"]=="global_zip" else f"{p}_R{row}_C{col}"

def valid_zip(path):
 try:
  with zipfile.ZipFile(path) as z: return z.testzip() is None
 except (OSError,zipfile.BadZipFile): return False

def download(layer,row=None,col=None):
 c=LAYERS[layer]; name=tile_name(layer,row,col)
 if layer=="pop":
  old=os.path.join(POP_TILE_DIR,name+".zip")
  if valid_zip(old): return old,False
 directory=os.path.join(TILE_DIR,layer); os.makedirs(directory,exist_ok=True)
 path=os.path.join(directory,name+".zip")
 if valid_zip(path): return path,False
 part=path+".part"; url=BASE_URL+c["folder"]+name+".zip"
 for attempt in range(1,DOWNLOAD_ATTEMPTS+1):
  result=subprocess.run(["curl","-fL","-C","-","--speed-limit","20000","--speed-time","60","--connect-timeout","30","--retry","0","-w","\n%{http_code}","-o",part,url],text=True,capture_output=True)
  lines=result.stdout.rstrip().splitlines(); http=lines[-1] if lines else ""
  if result.returncode==0 and valid_zip(part):
   os.replace(part,path); print(f"downloaded layer={layer} zip={name}.zip bytes={os.path.getsize(path)}",flush=True); return path,True
  if http=="404":
   if os.path.exists(part): os.remove(part)
   raise MissingTile(name)
  if result.returncode==33 and os.path.exists(part): os.remove(part)
  print(f"download_retry layer={layer} tile={name} attempt={attempt} http={http} curl={result.returncode}",flush=True)
  if attempt<DOWNLOAD_ATTEMPTS: time.sleep(DOWNLOAD_PAUSE_SECONDS)
 raise RuntimeError(f"failed after {DOWNLOAD_ATTEMPTS} attempts: {name}")

def tif_member(path):
 with zipfile.ZipFile(path) as z: names=[n for n in z.namelist() if n.lower().endswith(".tif")]
 if len(names)!=1: raise RuntimeError(f"expected one tif in {path}, found {names}")
 return names[0]

def tif_bytes(path):
 with zipfile.ZipFile(path) as z: return z.read(tif_member(path))

def extracted_tif(path):
 out=os.path.splitext(path)[0]+".tif"
 if os.path.exists(out): return out
 tmp=out+".tmp"
 with zipfile.ZipFile(path) as z, z.open(tif_member(path)) as source, open(tmp,"wb") as target: shutil.copyfileobj(source,target,1024*1024)
 os.replace(tmp,out); return out

def extract_documents(layer,path):
 if layer not in DOC_LAYERS: return
 directory=os.path.join(DOCS_DIR,layer); os.makedirs(directory,exist_ok=True)
 with zipfile.ZipFile(path) as z:
  for info in z.infolist():
   if info.is_dir() or info.filename.lower().endswith(".tif"): continue
   out=os.path.join(directory,os.path.basename(info.filename))
   if os.path.exists(out): continue
   tmp=out+".tmp"
   with z.open(info) as source,open(tmp,"wb") as target: shutil.copyfileobj(source,target,1024*1024)
   os.replace(tmp,out)

def box_for(venue,layer):
 x,y=TO_MOLL.transform(venue["lng"],venue["lat"]); h=LAYERS[layer]["half_box_m"]
 return x-h,y-h,x+h,y+h

def assert_source(ds,layer):
 cell=LAYERS[layer]["cell"]
 if not ds.crs or ds.crs.to_string()!="ESRI:54009": raise RuntimeError(f"unexpected CRS layer={layer}: {ds.crs}")
 if abs(ds.res[0]-cell)>=1e-6 or abs(ds.res[1]-cell)>=1e-6: raise RuntimeError(f"unexpected resolution layer={layer}: {ds.res}")

def grid_window(box,ox,oy,cell):
 l,b,r,t=box
 return math.floor((l-ox)/cell),math.floor((oy-t)/cell),math.ceil((r-ox)/cell),math.ceil((oy-b)/cell)

def write_crop(layer,slug,box,datasets):
 directory=os.path.join(CACHE_DIR,layer); os.makedirs(directory,exist_ok=True); out=os.path.join(directory,slug+".tif")
 if os.path.exists(out): return out
 for ds in datasets: assert_source(ds,layer)
 dtypes={ds.dtypes[0] for ds in datasets}; nodatas={ds.nodata for ds in datasets}
 if len(dtypes)!=1 or len(nodatas)!=1: raise RuntimeError(f"source type mismatch layer={layer}: {dtypes} {nodatas}")
 cell=LAYERS[layer]["cell"]; ox=datasets[0].transform.c; oy=datasets[0].transform.f
 for ds in datasets[1:]:
  xc=(ds.transform.c-ox)/cell; yr=(oy-ds.transform.f)/cell
  if abs(xc-round(xc))>1e-6 or abs(yr-round(yr))>1e-6: raise RuntimeError(f"tile grid mismatch layer={layer}: {xc} {yr}")
 fc,fr,ec,er=grid_window(box,ox,oy,cell); height,width=er-fr,ec-fc; nodata=datasets[0].nodata
 if nodata is None and len(datasets)>1: raise RuntimeError(f"cannot fill uncovered cells without nodata layer={layer}")
 array=np.full((height,width),nodata if nodata is not None else 0,dtype=datasets[0].dtypes[0]); covered=np.zeros((height,width),bool)
 for ds in datasets:
  dc=round((ds.transform.c-ox)/cell); dr=round((oy-ds.transform.f)/cell)
  left,top=max(fc,dc),max(fr,dr); right,bottom=min(ec,dc+ds.width),min(er,dr+ds.height)
  if left>=right or top>=bottom: continue
  values=ds.read(1,window=Window(left-dc,top-dr,right-left,bottom-top))
  rows=slice(top-fr,bottom-fr); cols=slice(left-fc,right-fc); array[rows,cols]=values; covered[rows,cols]=True
 if not covered.all() and nodata is None: raise RuntimeError(f"crop has uncovered cells and no nodata layer={layer} slug={slug}")
 transform=rasterio.Affine(cell,0,ox+fc*cell,0,-cell,oy-fr*cell); profile=datasets[0].profile.copy()
 profile.update(driver="GTiff",dtype=datasets[0].dtypes[0],count=1,height=height,width=width,transform=transform,compress="deflate",nodata=nodata)
 tmp=out+".tmp"
 with rasterio.open(tmp,"w",**profile) as target: target.write(array,1)
 os.replace(tmp,out); return out

def crop_zip_sources(layer,slug,box,pairs):
 out=os.path.join(CACHE_DIR,layer,slug+".tif")
 if os.path.exists(out): return out
 owners=[]; datasets=[]
 try:
  for _,path in pairs:
   if LAYERS[layer]["mode"]=="extracted_tile": ds=rasterio.open(extracted_tif(path)); owners.append(ds)
   else: memory=MemoryFile(tif_bytes(path)); ds=memory.open(); owners.extend([ds,memory])
   datasets.append(ds)
  return write_crop(layer,slug,box,datasets)
 finally:
  for owner in owners: owner.close()

def fun_url():
 c=LAYERS["fun"]; return "/vsicurl/"+BASE_URL+c["folder"]+c["prefix"]+".tif"

def crop_fun(slug,box):
 out=os.path.join(CACHE_DIR,"fun",slug+".tif")
 if os.path.exists(out): return out
 for attempt in range(1,FUN_ATTEMPTS+1):
  try:
   with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",GDAL_HTTP_TIMEOUT="60"):
    with rasterio.open(fun_url()) as ds: return write_crop("fun",slug,box,[ds])
  except Exception as error:
   print(f"read_retry layer=fun slug={slug} attempt={attempt} error={error}",flush=True)
   if attempt<FUN_ATTEMPTS: time.sleep(FUN_PAUSE_SECONDS)
 return None

def raster_row(slug,layer,tiles,path,status="ok"):
 row={field:"" for field in FIELDS}; row.update(slug=slug,layer=layer,tiles=";".join(tiles),status=status)
 if status!="ok" or not os.path.exists(path): return row
 with rasterio.open(path) as source:
  values=source.read(1); invalid=~np.isfinite(values)
  if source.nodata is not None: invalid|=values==source.nodata
  valid=values[~invalid]; row.update(width=source.width,height=source.height,dtype=source.dtypes[0],nodata="" if source.nodata is None else source.nodata,nodata_cells=int(invalid.sum()),valid_cells=int(valid.size),n_distinct=int(np.unique(valid).size))
  if valid.size: row.update(min=np.min(valid).item(),max=np.max(valid).item(),mean=float(np.mean(valid,dtype=np.float64)))
 return row

def manifest_tiles(layer,box):
 if layer=="fun": return ["global_tif"]
 if LAYERS[layer]["mode"]=="global_zip": return [tile_name(layer)]
 return [tile_name(layer,*coord) for coord in needed_tiles(box)]

def disk_manifest(venues,layers,statuses):
 rows=[]
 for venue in venues:
  for layer in layers:
   slug=venue["slug"]; box=box_for(venue,layer); path=os.path.join(CACHE_DIR,layer,slug+".tif")
   rows.append(raster_row(slug,layer,manifest_tiles(layer,box),path,statuses.get((slug,layer),"ok")))
 os.makedirs(os.path.dirname(MANIFEST_PATH),exist_ok=True); tmp=MANIFEST_PATH+".tmp"
 with open(tmp,"w",newline="",encoding="utf-8") as handle:
  writer=csv.DictWriter(handle,fieldnames=FIELDS); writer.writeheader(); writer.writerows(rows)
 os.replace(tmp,MANIFEST_PATH)

def parse_args():
 parser=argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group(required=True)
 group.add_argument("--venues",help="comma-separated canonical slugs"); group.add_argument("--all",action="store_true",help="process all venues")
 parser.add_argument("--layers",default=",".join(LAYERS),help="comma-separated layer keys"); return parser.parse_args()

def main():
 args=parse_args(); layers=[x for x in args.layers.split(",") if x]; unknown=[x for x in layers if x not in LAYERS]
 if unknown: raise SystemExit(f"unknown layer(s): {','.join(unknown)}")
 with open(INDEX_PATH,encoding="utf-8") as handle: venues=json.load(handle)
 by_slug={v["slug"]:v for v in venues}
 if args.all: selected=venues
 else:
  slugs=[x for x in args.venues.split(",") if x]; missing=[x for x in slugs if x not in by_slug]
  if missing: raise SystemExit(f"slug not found: {','.join(missing)}")
  selected=[by_slug[x] for x in slugs]
 downloaded=0; statuses={}; global_paths={}
 if "smod" in layers:
  try:
   path,changed=download("smod"); global_paths["smod"]=path; downloaded+=int(changed); extract_documents("smod",path)
   if changed: time.sleep(DOWNLOAD_PAUSE_SECONDS)
  except MissingTile: statuses.update({(v["slug"],"smod"):"missing_tile" for v in selected})
 docs_done=set()
 for number,venue in enumerate(selected,1):
  slug=venue["slug"]
  for layer in layers:
   if (slug,layer) in statuses: continue
   box=box_for(venue,layer)
   if layer=="fun":
    output=crop_fun(slug,box)
    if output is None: statuses[(slug,layer)]="read_failed"; continue
   else:
    coords=[(None,None)] if LAYERS[layer]["mode"]=="global_zip" else needed_tiles(box); pairs=[]
    try:
     for coord in coords:
      if LAYERS[layer]["mode"]=="global_zip": path,changed=global_paths[layer],False
      else: path,changed=download(layer,*coord)
      downloaded+=int(changed); pairs.append((coord,path))
      if layer in DOC_LAYERS and layer not in docs_done: extract_documents(layer,path); docs_done.add(layer)
      if changed: time.sleep(DOWNLOAD_PAUSE_SECONDS)
    except MissingTile as error:
     print(f"missing_tile slug={slug} layer={layer} tile={error}",flush=True); statuses[(slug,layer)]="missing_tile"; continue
    output=crop_zip_sources(layer,slug,box,pairs)
   print(f"venue={number}/{len(selected)} slug={slug} layer={layer} crop={output}",flush=True)
 disk_manifest(selected,layers,statuses); tile_bytes=0
 if os.path.isdir(TILE_DIR):
  for directory,_,files in os.walk(TILE_DIR): tile_bytes+=sum(os.path.getsize(os.path.join(directory,n)) for n in files)
 print(f"downloaded={downloaded} tile_cache_bytes={tile_bytes}"); return 0

if __name__=="__main__": raise SystemExit(main())
