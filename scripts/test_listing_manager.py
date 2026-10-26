import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; M=ROOT/'scripts/listing_manager.py'
def run(*args): return subprocess.run([sys.executable,str(M),*args],cwd=ROOT,text=True,capture_output=True)
def main():
 r=run('validate'); assert r.returncode==0 and 'VALIDATION PASS' in r.stdout
 r=run('build'); assert r.returncode==0 and (ROOT/'listings/nha-xanh-co-ban-cu-ebur/index.html').is_file()
 x=json.loads((ROOT/'data/listings.json').read_text())[0]; assert x['price']==1850000000 and x['width']==5 and x['length']==27
 print('3 tests passed')
if __name__=='__main__': main()
