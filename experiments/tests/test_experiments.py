import base64
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from experiments.bridge import Bridge, serve
from experiments.config import validate, parse_instruction, apply_patch, DEFAULT_PROFILE, digest
from experiments.engine import Engine
from experiments.events import Events, callback_parts
from experiments.resources import Services, birth, owned, stop_owned
from experiments.snapshots import ROOT, load_context, session_command, git
from experiments.profile import pin_arguments
from experiments.store import Store, write_json


def specification(repo):
    return {'title':'Synthetic isolation test', 'repo':str(repo), 'base_ref':'HEAD', 'task':'Change answer.txt to done',
        'acceptance':['The answer is done'], 'checks':[[sys.executable,'-c',"from pathlib import Path; assert Path('answer.txt').read_text() == 'done'"]],
        'groups':[{'id':g, 'profile':{}} for g in ('A','B','C')], 'max_parallel':2}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        git(self.repo,'init','-q'); git(self.repo,'config','user.name','Fixture'); git(self.repo,'config','user.email','fixture@example.invalid')
        (self.repo/'answer.txt').write_text('before')
        git(self.repo,'add','.'); git(self.repo,'commit','-qm','fixture')
        self.engine=Engine(self.root/'state',[self.repo]); self.bridge=Bridge(self.engine)
    def draft(self): return self.engine.create(specification(self.repo))
    def run_config(self):
        item=self.draft(); return self.engine.enqueue(item['id'],item['revision'],'test-start')


class ConfigTests(Fixture):
    def test_n_groups_and_transactional_language(self):
        value=self.draft()['config']
        result=apply_patch(value,parse_instruction('复制 A组 为 D组；D 用 Fable 5 medium；并行 3 组'))
        self.assertEqual(len(result['groups']),4); self.assertEqual(result['groups'][-1]['profile']['effort'],'medium')
        self.assertEqual(len(value['groups']),3)
        with self.assertRaises(ValueError): parse_instruction('并行2组；还有随便改改')
    def test_strict_configuration(self):
        for change in ({'max_parallel':4}, {'max_parallel':True}, {'checks':['echo pass']}, {'extra':'bad'}):
            with self.subTest(change=change),self.assertRaises(ValueError): validate(specification(self.repo)|change)
    def test_case_insensitive_ids(self):
        value=specification(self.repo);value['groups']=[{'id':'A'},{'id':'a'}]
        with self.assertRaises(ValueError): validate(value)
    def test_optimistic_revision_and_history(self):
        item=self.draft(); changed=self.engine.update(item['id'],1,instruction='B 用 medium')
        self.assertEqual(changed['revision'],2)
        self.assertEqual(self.engine.store.read('revisions',item['id']+'-r1')['groups'][1]['profile']['effort'],'max')
        with self.assertRaises(ValueError): self.engine.update(item['id'],1,instruction='B 用 high')
    def test_repository_boundary(self):
        with self.assertRaises(ValueError): self.engine.create(specification(self.root))
    def test_freeze_isolated_idempotent_tamper(self):
        item=self.run_config(); duplicate=self.engine.enqueue(item['id'],1,'test-start')
        self.assertEqual([a['id'] for a in item['attempts']],[a['id'] for a in duplicate['attempts']])
        self.assertEqual(len({a['branch'] for a in item['attempts']}),3)
        self.assertEqual(len({a['supervisor_session'] for a in item['attempts']}),3)
        a,b=item['attempts'][:2];Path(a['checkout'],'answer.txt').write_text('A')
        self.assertEqual(Path(b['checkout'],'answer.txt').read_text(),'before')
        ctx=load_context(a['context_path'],a['context_hash'],a['checkout'])
        self.assertEqual(ctx['group_id'],'A')
        with self.assertRaises(ValueError): load_context(a['context_path'],a['context_hash'],b['checkout'])
        doc=Path(a['workflow']['plugin_dir'],'skills/_shared/plan.md');doc.write_text(doc.read_text()+'tamper')
        with self.assertRaises(ValueError): load_context(a['context_path'],a['context_hash'],a['checkout'])
        with self.assertRaises(ValueError): self.engine.update(item['id'],1,instruction='B 用 high')
    def test_profile_pin_and_native_children(self):
        a=self.run_config()['attempts'][0];ctx=json.loads(Path(a['context_path']).read_text())
        command,_=session_command(ctx,a['context_path']); self.assertEqual(pin_arguments(command[1:],ctx),command[1:])
        for flags in (['--model','claude-fable-5'],['--resume','old'],['--permission-mode','bypassPermissions'],['--settings','{"permissions":{}}']):
            with self.subTest(flags=flags),self.assertRaises(ValueError): pin_arguments(command[1:]+flags,ctx)
        ctx['profile']['subagents']=False
        result=pin_arguments(['--session-id',ctx['supervisor_session'],'--','hello'],ctx)
        self.assertEqual(result[-2:],['--','hello']);self.assertIn('Agent,Task',result)
    def test_old_rules_do_not_claim_arbitrary_model(self):
        from experiments.snapshots import workflow_snapshot
        with self.assertRaises(ValueError): workflow_snapshot(self.root/'old',DEFAULT_PROFILE|{'workflow_ref':'workflow-v1.2.0','model':'claude-fable-5'})


class FakeHerdr:
    session='synthetic-session';caller='synthetic-pane'
    def __init__(self, engine): self.engine=engine;self.created=[];self.prompts=[];self.stops=[];self.states={}
    def create(self,a,c):
        self.created.append(a['id']);self.states[a['id']]='idle'
        return {'session':self.session,'pane':a['id'],'name':a['id']}
    def start(self,*args): pass
    def inspect(self,t): return {'status':self.states[t['name']]}
    def prompt(self,t,text,before_submit=None):
        if before_submit: before_submit()
        self.prompts.append((t['name'],text));self.states[t['name']]='working'
    def stop(self,t): self.stops.append(t['name'])
    def cleanup(self,attempt,checkpoint): return bool(attempt.get('result_status'))


class QueueTests(Fixture):
    def test_three_groups_two_slots_and_success_unknown_usage(self):
        item=self.run_config();adapter=FakeHerdr(self.engine)
        self.engine.tick(adapter);self.assertEqual(len(adapter.created),2);self.assertEqual(len(adapter.prompts),0)
        self.engine.tick(adapter);self.assertEqual(len(adapter.prompts),2)
        first=item['attempts'][0];Path(first['checkout'],'answer.txt').write_text('done')
        git(first['checkout'],'add','.');git(first['checkout'],'commit','-qm','done')
        write_json(first['outcome_path'],{'status':'completed','summary':'synthetic result'})
        adapter.states[first['id']]='idle';self.engine.tick(adapter)
        updated=self.engine.get(item['id']);self.assertEqual(updated['attempts'][0]['status'],'completed')
        self.assertFalse(updated['attempts'][0]['comparison_eligible']);self.assertEqual(len(adapter.created),3)
        self.assertEqual(updated['attempts'][0]['acceptance']['checks'][0]['exit_code'],0)
    def test_uncertain_launch_never_retried(self):
        item=self.run_config();adapter=FakeHerdr(self.engine)
        def fail(*args): raise RuntimeError('uncertain transport')
        adapter.start=fail
        self.engine.tick(adapter);self.engine.tick(adapter)
        self.assertEqual(len(adapter.created),2);self.assertEqual(self.engine.get(item['id'])['attempts'][0]['status'],'needs_attention')
    def test_pending_stop_and_unknown_descendants_keep_slot(self):
        item=self.run_config();adapter=FakeHerdr(self.engine);self.engine.tick(adapter)
        self.engine.request_stop(item['id']);self.engine.tick(adapter);self.engine.tick(adapter)
        self.assertEqual(len(adapter.stops),2);self.assertEqual(len(adapter.created),2)
        self.assertEqual(self.engine.get(item['id'])['attempts'][2]['status'],'cancelled')
    def test_intervention_deduplicated_and_rate_bounded(self):
        item=self.run_config();adapter=FakeHerdr(self.engine);self.engine.tick(adapter)
        aid=item['attempts'][0]['id'];action=self.engine.request_instruction(item['id'],aid,'stay scoped','instruction-1')
        self.assertEqual(action,self.engine.request_instruction(item['id'],aid,'stay scoped','instruction-1'))
        with self.assertRaises(ValueError): self.engine.request_instruction(item['id'],aid,'again','instruction-2')
    def test_check_failure_preserved(self):
        item=self.run_config();adapter=FakeHerdr(self.engine);self.engine.tick(adapter);self.engine.tick(adapter)
        first=item['attempts'][0];write_json(first['outcome_path'],{'status':'completed','summary':'false claim'})
        adapter.states[first['id']]='idle';self.engine.tick(adapter)
        a=self.engine.get(item['id'])['attempts'][0];self.assertEqual(a['status'],'failed');self.assertFalse(a['acceptance']['checks'][0]['passed'])
    def test_slow_worker_does_not_lock_ui(self):
        item=self.run_config();adapter=FakeHerdr(self.engine);started=threading.Event();release=threading.Event()
        def block(*a): started.set();release.wait(5)
        adapter.start=block
        thread=threading.Thread(target=self.engine.tick,args=(adapter,));thread.start()
        try:
            self.assertTrue(started.wait(3));before=time.monotonic();self.engine.request_stop(item['id']);self.assertLess(time.monotonic()-before,1)
        finally: release.set();thread.join(10)
        self.assertTrue(self.engine.get(item['id'])['attempts'][0]['stop_requested'])
    def test_global_worker_cap(self):
        self.run_config();second=self.draft();self.engine.enqueue(second['id'],1,'other-start')
        adapter=FakeHerdr(self.engine);self.engine.tick(adapter,capacity=3);self.assertEqual(len(adapter.created),3)


class BridgeTests(Fixture):
    def test_rpc_error_boundaries_and_same_state(self):
        for params in (None,[],{'_meta':None}):
            self.assertIn('error',self.bridge.rpc({'jsonrpc':'2.0','id':1,'method':'tools/list','params':params}))
        result=self.bridge.rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'workflow_create','arguments':{'config':specification(self.repo)}}})
        eid=result['result']['structuredContent']['id'];self.assertEqual(self.engine.get(eid)['revision'],1)
        listing=self.bridge.rpc({'jsonrpc':'2.0','id':3,'method':'tools/list','params':{'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28'}}})
        self.assertEqual(listing['result']['resultType'],'complete')
    def test_http_auth_host_origin_and_malformed_body(self):
        server,_=serve(self.engine);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def req(path,headers={},body=None):
            r=urllib.request.Request(url+path,headers=headers,data=body)
            try:
                with opener.open(r) as response:return response.status,response.read()
            except urllib.error.HTTPError as e:return e.code,e.read()
        self.assertEqual(req('/api/status')[0],401)
        auth={'Authorization':'Bearer '+self.engine.store.token()}
        self.assertEqual(req('/api/status',auth)[0],200)
        self.assertEqual(req('/api/status',auth|{'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(req('/api/status',auth|{'Host':'attacker.invalid'})[0],403)
        self.assertEqual(req('/api/call',auth|{'Content-Type':'application/json'},b'[]')[0],400)
        self.assertEqual(req('/')[0],200)
    def test_events_verification_dedupe_and_finite_delivery(self):
        item=self.draft();calls=[]
        def sender(url,secret,sid,eid,payload):
            calls.append(payload)
            return (200,json.dumps({'challenge':payload['challenge']}).encode()) if payload.get('type')=='verification' else (200,b'{}')
        event=Events(self.engine,sender)
        params={'name':'attempt.blocked','arguments':{'experiment_id':item['id']},'delivery':{'mode':'webhook','url':'https://fixture.invalid/event','secret':'whsec_'+base64.b64encode(b'x'*32).decode()}}
        event.subscribe(params)
        self.engine.store.event('attempt.blocked',item['id'],'attempt-1',{'reason':'test'})
        self.engine.store.event('attempt.blocked',item['id'],'attempt-1',{'reason':'test'})
        self.assertEqual(event.deliver(),1);self.assertEqual(event.deliver(),0)
        event.unsubscribe(params);self.assertEqual(event.deliver(),0)
    def test_private_callbacks_rejected(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),self.assertRaises(ValueError): callback_parts('https://fixture.invalid/hook')
    def test_failed_webhook_is_bounded(self):
        item=self.draft()
        def sender(url,secret,sid,eid,payload):return (200,json.dumps({'challenge':payload['challenge']}).encode()) if payload.get('type')=='verification' else (500,b'no')
        event=Events(self.engine,sender);p={'name':'attempt.failed','arguments':{'experiment_id':item['id']},'delivery':{'mode':'webhook','url':'https://fixture.invalid','secret':'whsec_'+base64.b64encode(b'x'*32).decode()}}
        sid=event.subscribe(p)['id'];self.engine.store.event('attempt.failed',item['id'])
        for _ in range(5):
            event.deliver();sub=self.engine.store.read('subscriptions',sid)
            for d in sub['deliveries'].values(): d['next_at']=0
            self.engine.store.save('subscriptions',sid,sub)
        self.assertEqual(next(iter(sub['deliveries'].values()))['attempts'],3)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.manager=Services(Store(self.root/'state'));self.attempt={'id':'attempt-A','directory':str(self.root),'checkout':str(self.root),'data_dir':str(self.root)}
        self.addCleanup(self.manager.stop,self.attempt['id'])
    def specs(self,port=0):return [{'name':'web','argv':[sys.executable,'-m','http.server','{port}','--bind','127.0.0.1'],'port_env':'WEB_PORT','health_path':'/','preferred_port':port}]
    def test_occupied_port_is_reassigned_and_foreign_owner_survives(self):
        foreign=socket.socket();foreign.bind(('127.0.0.1',0));foreign.listen();self.addCleanup(foreign.close)
        lease=self.manager.start(self.attempt,self.specs(foreign.getsockname()[1]))['web']
        self.assertNotEqual(lease['port'],foreign.getsockname()[1]);self.assertTrue(owned(lease['process']))
        self.manager.stop(self.attempt['id']);self.assertEqual(self.manager._read(self.attempt['id']),{});self.assertGreater(foreign.fileno(),0)
    def test_concurrent_bundle_allocation_and_recovery(self):
        results=[];errors=[]
        def run(n):
            try:results.append(self.manager.start(self.attempt|{'id':'attempt-'+str(n)},self.specs()))
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=run,args=(i,)) for i in range(3)]
        for t in threads:t.start()
        for t in threads:t.join(30)
        try:
            self.assertFalse(errors);self.assertEqual(len({r['web']['port'] for r in results}),3)
            self.assertEqual(self.manager.recover(),[])
        finally:
            for i in range(3):self.manager.stop('attempt-'+str(i))
    def test_never_kill_reused_identity(self):
        self.assertFalse(stop_owned({'pid':os.getpid(),'birth':'not-my-birth'}))
    def test_dead_leases_recovered(self):
        self.manager._save('attempt-A',{'web':{'port':12345,'process':{'pid':99999999,'birth':'dead'}}})
        self.assertEqual(self.manager.recover(),['attempt-A'])


if __name__=='__main__':unittest.main()

class EvidenceTests(unittest.TestCase):
    def test_native_identity_edges_and_duplicate_usage(self):
        from experiments.evidence import descendants
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid='11111111-1111-4111-8111-111111111111';parent=root/(sid+'.jsonl');parent.write_text('{}')
            folder=root/sid/'subagents';folder.mkdir(parents=True)
            row={'type':'assistant','sessionId':sid,'agentId':'child','isSidechain':True,'parentAgentId':'parent',
                'message':{'id':'msg-1','model':'claude-opus-5-5','content':[], 'usage':{'input_tokens':2,'output_tokens':3,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}}
            (folder/'agent-child.jsonl').write_text(json.dumps(row)+'\n')
            data={'claude':{'source':{'path':str(parent)},'usage_index':[]},'claude_threads':[]}
            result=descendants(data);self.assertEqual(result['threads'][0]['parent_agent_id'],'parent')
            self.assertEqual(result['observed_usage_sum']['output_tokens'],3)
            data['claude']['usage_index']=[{'message_id':'msg-1'}];self.assertIsNone(descendants(data)['observed_usage_sum'])
            row['sessionId']='foreign';(folder/'agent-foreign.jsonl').write_text(json.dumps(row)+'\n')
            self.assertEqual(len(descendants(data)['threads']),1);self.assertTrue(descendants(data)['unknowns'])
    def test_composer_guard_never_overwrites_drafts_or_dialogs(self):
        from experiments.herdr import empty_input
        self.assertTrue(empty_input({'output':'Claude\n────────\n❯\n────────\n? for shortcuts'}))
        for screen in ('❯ unfinished instruction','Allow this operation?\n❯','hello',{'unknown':'❯'},'❯\nwrapped draft'):
            self.assertFalse(empty_input(screen))

class RecoveryTests(Fixture):
    def test_stale_freeze_retains_provisioning(self):
        item=self.draft();item.update(status='preparing',provisioning=[{'directory':'recorded-path'}]);self.engine.store.save('experiments',item['id'],item)
        self.engine.tick(FakeHerdr(self.engine));result=self.engine.get(item['id'])
        self.assertEqual(result['status'],'failed');self.assertEqual(result['provisioning'],item['provisioning'])
    def test_stdio_client_and_generated_plugin(self):
        command=[sys.executable,str(ROOT/'scripts/experiment.py'),'--state-dir',str(self.root/'state'),'--allow-repo',str(self.repo)]
        subprocess.run(command+['connection','--write-plugin'],check=True,capture_output=True)
        plugin=json.loads((self.root/'state/dot-plugin/mcp.json').read_text())
        self.assertEqual(plugin['mcpServers']['herdr-experiments']['type'],'stdio')
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','clientInfo':{'name':'fixture','version':'1'},'capabilities':{}}},
                  {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'workflow_status','arguments':{}}}]
        result=subprocess.run(command+['mcp'],input=''.join(json.dumps(m)+'\n' for m in messages),capture_output=True,text=True,check=True)
        rows=[json.loads(x) for x in result.stdout.splitlines()];self.assertEqual(rows[0]['result']['protocolVersion'],'2025-11-25')
        self.assertFalse(rows[1]['result']['structuredContent']['dispatcher_connected'])
