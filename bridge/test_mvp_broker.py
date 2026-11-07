import json,tempfile,unittest
from pathlib import Path
from mvp_broker import load,validate,digest,process
class M(unittest.TestCase):
 def candidate(self):
  c={'schema_version':'bridge-mvp-v1','candidate_id':'bridge-mvp-1-e','repository':'Haisala/sala-bmt-listings','issue_number':1,'issue_url':'https://github.com/Haisala/sala-bmt-listings/issues/1','event_id':'e','event_name':'issues','task_id':'t','objective':'synthetic','acceptance_criteria':'no-op','status':'READY_FOR_BROKER','created_at':'2026-01-01T00:00:00+00:00','expires_at':'2999-01-01T00:00:00+00:00'}; c['task_digest']=digest(c); return c
 def test_valid_schema(self): self.assertEqual(validate(self.candidate()),(True,''))
 def test_digest_tamper(self): c=self.candidate();c['objective']='x';self.assertEqual(validate(c)[1],'DIGEST_MISMATCH')
 def test_kill_switch_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   root=load(Path(d)); (root/'control'/'kill_switch.json').write_text('{bad'); p=root/'inbox'/'c.json';p.write_text(json.dumps(self.candidate())); self.assertEqual(process(root,p)[0],'BLOCKED')
 def test_acceptance_list_required(self):\n  c=self.candidate(); c['acceptance_criteria']='no-op'; self.assertEqual(validate(c)[1],'CANDIDATE_POLICY_REJECTED')\n def test_malformed_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=load(Path(d));p=root/'inbox'/'c.json';p.write_text('{}');self.assertEqual(process(root,p)[0],'REJECTED')
if __name__=='__main__':unittest.main()
