#!/usr/bin/env python3
"""Bridge MVP one-shot broker. Bounded synthetic task only."""
import argparse,datetime as dt,hashlib,json,os,pathlib,shutil,subprocess,sys,tempfile,time
REPO='Haisala/sala-bmt-listings'; SCHEMA='bridge-mvp-v1'; ACK='BRIDGE_MVP_AUTO_PICKUP_ACK'
ROOTS=('inbox','claimed','completed','blocked','receipts','control')
def now(): return dt.datetime.now(dt.timezone.utc)
def iso(x): return x.astimezone(dt.timezone.utc).isoformat()
def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(c):
 keys=('repository','issue_number','issue_url','event_id','event_name','task_id','objective','acceptance_criteria')
 return 'sha256:'+hashlib.sha256(canon({k:c[k] for k in keys}).encode()).hexdigest()
def safe(s): return '[REDACTED]' if any(x in s.lower() for x in ('token=','password=','secret=','bearer ')) else str(s)[:200]
def load(root):
 p=pathlib.Path(root); p.mkdir(parents=True,exist_ok=True)
 for x in ROOTS:(p/x).mkdir(exist_ok=True)
 (p/'workspaces').mkdir(exist_ok=True)
 return p
def receipt(root,key,**kw):
 p=root/'receipts'/(hashlib.sha256(key.encode()).hexdigest()+'.json');
 if p.exists(): return json.loads(p.read_text())
 d={'claim_key':key,'codex_attempts':0,**kw}; tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(d,sort_keys=True,indent=2)+'\n'); os.replace(tmp,p); return d
def kill(root):
 p=root/'control'/'kill_switch.json'
 if not p.exists(): return True,'KILL_SWITCH_UNAVAILABLE'
 try:
  d=json.loads(p.read_text()); return bool(d.get('enabled') is True),'KILL_SWITCH_ENABLED' if d.get('enabled') is True else ''
 except Exception:return True,'KILL_SWITCH_UNAVAILABLE'
def validate(c):
 req=('schema_version','candidate_id','repository','issue_number','issue_url','event_id','event_name','task_id','objective','acceptance_criteria','task_digest','status','created_at','expires_at')
 if any(k not in c for k in req): return False,'MALFORMED_CANDIDATE'
 if c['schema_version']!=SCHEMA or c['repository']!=REPO or c['status']!='READY_FOR_BROKER' or not isinstance(c.get('acceptance_criteria'),list) or not c['acceptance_criteria'] or not c.get('scope'): return False,'CANDIDATE_POLICY_REJECTED'
 if c['task_digest']!=digest(c): return False,'DIGEST_MISMATCH'
 try:
  if dt.datetime.fromisoformat(c['expires_at'].replace('Z','+00:00'))<=now(): return False,'CANDIDATE_EXPIRED'
 except Exception:return False,'INVALID_EXPIRY'
 return True,''
def codex(root,cid):
 w=root/'workspaces'/cid; w.mkdir(parents=True,exist_ok=True); before=set(w.iterdir())
 prompt='Return exactly BRIDGE_MVP_AUTO_PICKUP_ACK. Do not modify files. Do not access customer data. Do not make network requests. Do not run shell commands.'
 try:
  p=subprocess.run(['codex','exec','--ephemeral','--sandbox','read-only','-C',str(w),prompt],capture_output=True,text=True,timeout=60)
 except subprocess.TimeoutExpired:return 1,'TIMEOUT',False
 changed=set(w.iterdir())-before
 out=p.stdout.strip(); return p.returncode,out,(p.returncode==0 and out==ACK and not changed)
def result_text(c,status,reason,ack=None):
 return '\n'.join(['BRIDGE_MVP_RESULT',f"TASK_ID: {c.get('task_id','')}",f"ISSUE: #{c.get('issue_number','')}",f"STATUS: {status}",f"CODEX_ACK: {ack or 'NONE'}",f"ATTEMPTS: {1 if ack else 0}",'FILES_CHANGED: 0',f"REASON: {safe(reason)}",f"NEXT_ACTION: {'Owner review' if ack else 'Manual review'}"])
def post_comment(repo,issue,body):
 try:
  q=subprocess.run(['gh','issue','view',str(issue),'--repo',repo,'--json','comments'],capture_output=True,text=True,timeout=30,check=True)
  comments=json.loads(q.stdout).get('comments',[])
  if any('BRIDGE_MVP_RESULT' in (x.get('body') or '') for x in comments): return 'ALREADY_PRESENT'
  subprocess.run(['gh','issue','comment',str(issue),'--repo',repo,'--body',body],capture_output=True,text=True,timeout=30,check=True)
  return 'POSTED'
 except Exception:return 'RESULT_REPORT_BLOCKED'
def process(root,fp,post=False,repo=REPO):
 try:c=json.loads(fp.read_text(encoding='utf-8'))
 except Exception:return 'REJECTED','MALFORMED_JSON'
 ok,reason=validate(c); key=f"{c.get('repository')}|{c.get('issue_number')}|{c.get('task_digest')}"
 if not ok:return 'REJECTED',reason
 rp=root/'receipts'/(hashlib.sha256(key.encode()).hexdigest()+'.json')
 if rp.exists():return 'DUPLICATE_IGNORED','CLAIM_ALREADY_RECORDED'
 blocked,breason=kill(root)
 if blocked:return 'BLOCKED',breason
 claimed=root/'claimed'/fp.name; os.replace(fp,claimed)
 r=receipt(root,key,candidate_id=c['candidate_id'],issue_number=c['issue_number'],task_digest=c['task_digest'],status='CLAIMED',claimed_at=iso(now()),completed_at=None,result=None,error=None)
 blocked,breason=kill(root)
 if blocked:r.update(status='BLOCKED',error=breason); rp.write_text(json.dumps(r,indent=2)+'\n'); os.replace(claimed,root/'blocked'/claimed.name); return 'BLOCKED',breason
 blocked,breason=kill(root)
 if blocked:r.update(status='BLOCKED',error=breason); rp.write_text(json.dumps(r,indent=2)+'\n'); os.replace(claimed,root/'blocked'/claimed.name); return 'BLOCKED',breason
 r['codex_attempts']=1; out,raw,success=codex(root,c['candidate_id']); r.update(status='SUCCEEDED' if success else 'BLOCKED',completed_at=iso(now()),result=ACK if success else None,error=None if success else safe(raw)); rp.write_text(json.dumps(r,sort_keys=True,indent=2)+'\n'); os.replace(claimed,root/('completed' if success else 'blocked')/claimed.name)
 if post:
  cm=post_comment(repo,c['issue_number'],result_text(c,r['status'],r.get('error') or 'synthetic_no_op_completed',ACK if success else None));
  if cm=='RESULT_REPORT_BLOCKED': return 'RESULT_REPORT_BLOCKED','comment_failed'
 return r['status'],r.get('error') or ACK
def fetch_artifact(repository,run_id,artifact,root):
 d=root/'inbox'; tmp=root/'artifact-download'; tmp.mkdir(exist_ok=True)
 r=subprocess.run(['gh','run','download',str(run_id),'--repo',repository,'--name',artifact,'--dir',str(tmp)],capture_output=True,text=True,timeout=60)
 if r.returncode!=0: raise RuntimeError('CANDIDATE_ARTIFACT_TRANSPORT_UNAVAILABLE')
 src=tmp/'candidate.json';
 if not src.exists(): raise RuntimeError('CANDIDATE_ARTIFACT_TRANSPORT_UNAVAILABLE')
 dst=d/(artifact+'.json'); os.replace(src,dst); return dst
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--inbox',default='bridge_runtime/inbox'); ap.add_argument('--repository',default=None); ap.add_argument('--workflow-run',default=None); ap.add_argument('--artifact',default=None); ap.add_argument('--post-comment',action='store_true'); ap.add_argument('--poll-interval',type=int,default=0); ap.add_argument('--max-cycles',type=int,default=1); ap.add_argument('--once',action='store_true'); args=ap.parse_args(); root=load(pathlib.Path(args.inbox).parent)
 if args.repository or args.workflow_run or args.artifact:
  if not (args.repository and args.workflow_run and args.artifact): print(json.dumps({'status':'BLOCKED','reason':'CANDIDATE_ARTIFACT_TRANSPORT_UNAVAILABLE'})); return 2
  try: fetch_artifact(args.repository,args.workflow_run,args.artifact,root)
  except Exception as e: print(json.dumps({'status':'BLOCKED','reason':str(e)})); return 2
 for i in range(1 if args.once else args.max_cycles):
  files=sorted(p for p in (root/'inbox').rglob('candidate.json') if not any(part in {'claimed','completed','blocked','receipts'} for part in p.parts))
  for f in files: print(json.dumps({'status':process(root,f,args.post_comment,args.repository or REPO)}))
  if not args.once and i+1<args.max_cycles: time.sleep(args.poll_interval)
if __name__=='__main__':main()
