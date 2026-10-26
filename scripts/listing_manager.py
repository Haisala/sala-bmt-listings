#!/usr/bin/env python3
"""Standard-library listing manager: add/update/status/validate/build/publish."""
from __future__ import annotations
import argparse, datetime as dt, json, re, subprocess, unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data/listings.json'; SITE='https://haisala.github.io/sala-bmt-listings'; REMOTE='https://github.com/Haisala/sala-bmt-listings.git'; STATUSES={'Đang bán','Đã cọc','Đã bán'}
def now(): return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def load():
 try: x=json.loads(DATA.read_text(encoding='utf-8'))
 except Exception as e: raise SystemExit(f'FAIL data/listings.json: {e}')
 if not isinstance(x,list): raise SystemExit('FAIL listings must be an array')
 return x
def save(x): DATA.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def slugify(s): return re.sub(r'[^a-z0-9]+','-',unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower()).strip('-')
def validate(items):
 errors=[]; warnings=[]; slugs=set()
 for i,x in enumerate(items):
  p=f'listing[{i}]'
  if not isinstance(x,dict): errors.append(f'{p} must be object'); continue
  if not x.get('slug') or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',x.get('slug','')): errors.append(f'{p}.slug invalid')
  if x.get('slug') in slugs: errors.append(f'duplicate slug: {x.get("slug")}')
  slugs.add(x.get('slug'))
  for k in ('title','status','updatedAt'):
   if not x.get(k): errors.append(f'{p}.{k} required')
  if not isinstance(x.get('price'),(int,float)) or x.get('price',0)<=0: errors.append(f'{p}.price must be > 0')
  if x.get('status') not in STATUSES: errors.append(f'{p}.status invalid')
  if not isinstance(x.get('features',[]),list) or not isinstance(x.get('images',[]),list): errors.append(f'{p}.features/images must be arrays')
  for image in x.get('images',[]):
   if not (ROOT/image).is_file(): errors.append(f'{p}.image missing: {image}')
  for k in ('bedrooms','bathrooms','constructionArea','roadWidth','contact','images'):
   if not x.get(k): warnings.append(f'{p}.{k} missing (optional)')
 if errors:
  print('VALIDATION FAIL'); print('\n'.join('- '+e for e in errors)); return False
 print(f'VALIDATION PASS: {len(items)} listing(s)')
 if warnings: print('WARNINGS:\n'+'\n'.join('- '+w for w in warnings))
 return True
def derived(x):
 w,l,a=x.get('width'),x.get('length'),x.get('landArea'); a=a or (w*l if isinstance(w,(int,float)) and isinstance(l,(int,float)) else None)
 if not isinstance(a,(int,float)) or a<=0: return {}
 p=x['price']; return {'landArea':a,'pricePerM2':round(p/a,2),'pricePerMeterFrontage':round(p/w,2) if w else None,'priceFor5mFrontage':round(p*5/w,2) if w else None}
def money(n): return f'{n:,.0f}'.replace(',','.')+' VNĐ'
def esc(s): return str(s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')
def render(x):
 d=derived(x); title=esc(x['title']); url=f'{SITE}/listings/{x["slug"]}/'; imgs=''.join(f'<img src="../../{esc(i)}" alt="{title}">' for i in x.get('images',[])) or '<div class="empty-media">Chưa có ảnh</div>'; facts=[]
 if d.get('landArea'): facts.append(f'Diện tích đất: {d["landArea"]:g}m² (tính từ kích thước cung cấp)')
 if x.get('width') and x.get('length'): facts.append(f'Kích thước: {x["width"]:g}×{x["length"]:g}m')
 facts += [f'Pháp lý: {x.get("legal","Chưa cung cấp")}']+list(x.get('features',[])); calc=''
 if d.get('pricePerM2'): calc=f'<p class="calc">Phép tính từ GIÁ CHÀO, không phải giá giao dịch: {money(d["pricePerM2"])} / m² đất; {money(d["pricePerMeterFrontage"])} / mét ngang; quy đổi 5m ngang: {money(d["priceFor5mFrontage"])}.</p>'
 ld={'@context':'https://schema.org','@type':'RealEstateListing','name':x['title'],'url':url,'dateModified':x.get('updatedAt'),'offers':{'@type':'Offer','price':x['price'],'priceCurrency':'VND'}}
 return f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | Sala BMT Listings</title><meta name="description" content="{esc(x.get('description') or title)}"><link rel="canonical" href="{url}"><meta property="og:title" content="{title}"><meta property="og:description" content="{esc(x.get('priceLabel') or money(x['price']))}"><meta property="og:type" content="website"><meta property="og:url" content="{url}"><link rel="stylesheet" href="../../styles.css"><script type="application/ld+json">{json.dumps(ld,ensure_ascii=False)}</script></head><body><header class="site-header"><a class="brand" href="../../">Sala BMT Listings</a><span>Chi tiết bất động sản</span></header><main><article class="detail"><a class="back" href="../../">← Về danh sách</a><p class="eyebrow">{esc(x.get('category'))} · {esc(x.get('status'))}</p><h1>{title}</h1><div class="gallery">{imgs}</div><p class="price">{esc(x.get('priceLabel') or money(x['price']))}</p><p class="notice"><strong>Lưu ý:</strong> {esc(x.get('priceNote') or 'Đây là giá chào, không phải giá giao dịch.')}</p><h2>Thông tin</h2><ul>{''.join(f'<li>{esc(v)}</li>' for v in facts)}</ul>{calc}<p>{esc(x.get('description'))}</p><p class="muted">Cập nhật: {esc(x.get('updatedAt'))}</p></article></main><footer>© Sala BMT Listings · Dữ liệu cần được xác minh trước khi giao dịch.</footer></body></html>'''
def build(items):
 out=ROOT/'listings'; out.mkdir(exist_ok=True)
 for x in items:
  d=out/x['slug']; d.mkdir(parents=True,exist_ok=True); (d/'index.html').write_text(render(x),encoding='utf-8')
 urls=[f'{SITE}/']+[f'{SITE}/listings/{x["slug"]}/' for x in items]; (ROOT/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join(f'<url><loc>{u}</loc></url>' for u in urls)+'</urlset>\n',encoding='utf-8'); print(f'BUILD PASS: {len(items)} page(s)')
def git(*a): return subprocess.run(['git',*a],cwd=ROOT,text=True,capture_output=True,check=True)
def safety():
 if git('rev-parse','--show-toplevel').stdout.strip()!=str(ROOT): raise SystemExit('FAIL CLOSED: wrong repository')
 if git('remote','get-url','origin').stdout.strip().rstrip('/')!=REMOTE.rstrip('/'): raise SystemExit('FAIL CLOSED: wrong origin')
def main():
 ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest='cmd',required=True)
 for n in ('add','update'): sp.add_parser(n).add_argument('payload')
 sp.add_parser('status').add_argument('slug'); [sp.add_parser(n) for n in ('validate','build','publish')]; a=ap.parse_args(); items=load()
 if a.cmd=='add':
  x=json.loads(Path(a.payload).read_text()); x.setdefault('slug',slugify(x.get('title','listing'))); x.setdefault('id',x['slug']); x.setdefault('status','Đang bán'); x.setdefault('features',[]); x.setdefault('images',[]); x.setdefault('createdAt',now()); x['updatedAt']=now(); items.append(x); save(items)
 elif a.cmd=='update':
  p=json.loads(Path(a.payload).read_text()); x=next((z for z in items if z.get('slug')==p.get('slug') or z.get('id')==p.get('id')),None)
  if not x: raise SystemExit('listing not found')
  x.update(p); x['updatedAt']=now(); save(items)
 elif a.cmd=='status':
  x=next((z for z in items if z.get('slug')==a.slug),None)
  if not x: raise SystemExit('listing not found')
  print(json.dumps({'listing':x,'derived':derived(x)},ensure_ascii=False,indent=2))
 elif a.cmd in ('validate','build','publish'):
  if not validate(items): raise SystemExit(1)
  if a.cmd in ('build','publish'): build(items)
  if a.cmd=='publish':
   safety(); changed=git('status','--short').stdout
   if changed: git('add','.') ; git('commit','-m','Update listings') ; git('push','origin','main')
   print('PUBLISH PASS')
if __name__=='__main__': main()
