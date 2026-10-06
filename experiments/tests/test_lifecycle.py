"""Regression scenarios from the real two-group run. All fixtures are zero-model."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from experiments.checks import resolve_checks
from experiments.config import digest
from experiments.lifecycle import completed_lanes, processes, registrations, shutdown, track
from experiments.snapshots import git
from experiments.store import write_json
from experiments.tests.test_experiments import Fixture, FakeHerdr, specification
from scripts.claude_lane_state import probe


class CompletionTests(Fixture):
    def test_connected_old_dispatcher_cannot_consume_new_run(self):
        from experiments.resources import birth
        write_json(self.engine.store.root/'worker.json',{'pid':os.getpid(),'birth':birth(os.getpid()),'session':'old'})
        item=self.draft()
        with self.assertRaisesRegex(ValueError,'older build'):
            self.engine.enqueue(item['id'],1,'new-run')
        self.assertEqual(self.engine.get(item['id'])['status'],'draft')

    def test_preflight_missing_command_leaves_draft_without_worktrees(self):
        spec = specification(self.repo); spec['checks'] = [['/missing/acceptance-tool']]
        item = self.engine.create(spec)
        with self.assertRaisesRegex(ValueError, 'before launch'):
            self.engine.enqueue(item['id'], 1, 'invalid-check')
        value = self.engine.get(item['id'])
        self.assertEqual(value['status'], 'draft')
        self.assertEqual(value['attempts'], [])

    def test_portable_test_preserves_original_argv(self):
        result = resolve_checks([['/usr/bin/test', '-f', 'file']], self.repo)[0]
        self.assertEqual(result['requested_argv'][0], '/usr/bin/test')
        self.assertTrue(os.access(result['argv'][0], os.X_OK))
        with self.assertRaises(ValueError): resolve_checks([], self.repo)

    def test_done_wakes_existing_supervisor_once_without_accepting(self):
        item = self.run_config(); adapter = FakeHerdr(self.engine)
        self.engine.tick(adapter); self.engine.tick(adapter)
        a = item['attempts'][0]; adapter.states[a['id']] = 'idle'
        lanes = [{'session': 'completed-fixture', 'checkout': a['lanes_dir']}]
        with patch('experiments.lifecycle.completed_lanes', return_value=lanes):
            self.engine.tick(adapter)
            adapter.states[a['id']] = 'idle'
            self.engine.tick(adapter)
        self.assertEqual(sum(t.startswith('/herdr-dispatch:dispatch-codex --resume') for _, t in adapter.prompts), 1)
        a = self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(next(iter(a['completion_wakes'].values()))['status'], 'submitted')
        self.assertNotEqual(a['status'], 'completed')
        self.assertFalse(Path(a['outcome_path']).exists())

    def test_draft_blocks_wake_without_clearing_or_resending(self):
        item = self.run_config(); adapter = FakeHerdr(self.engine)
        self.engine.tick(adapter); self.engine.tick(adapter)
        a = item['attempts'][0]; adapter.states[a['id']] = 'idle'
        adapter.prompt = Mock(side_effect=ValueError('Visible input is not confirmed empty; no text sent'))
        with patch('experiments.lifecycle.completed_lanes', return_value=[{'session': 'lane'}]):
            self.engine.tick(adapter); self.engine.tick(adapter)
        adapter.prompt.assert_called_once()
        a = self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(next(iter(a['completion_wakes'].values()))['status'], 'not_submitted')
        self.assertEqual(a['status'], 'needs_attention')

    def test_uncertain_wake_is_never_repeated_after_restart(self):
        item = self.run_config(); adapter = FakeHerdr(self.engine)
        self.engine.tick(adapter); self.engine.tick(adapter)
        a = item['attempts'][0]; adapter.states[a['id']] = 'idle'
        def uncertain(target, text, before_submit):
            before_submit(); raise RuntimeError('agent_prompt_stalled')
        adapter.prompt = Mock(side_effect=uncertain)
        with patch('experiments.lifecycle.completed_lanes', return_value=[{'session': 'lane'}]):
            self.engine.tick(adapter); self.engine.tick(adapter)
        adapter.prompt.assert_called_once()
        a = self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(next(iter(a['completion_wakes'].values()))['status'], 'uncertain')

    def test_acceptance_spawn_error_recorded_and_not_repeated(self):
        item = self.run_config(); adapter = FakeHerdr(self.engine)
        self.engine.tick(adapter); self.engine.tick(adapter)
        item = self.engine.get(item['id']); a = item['attempts'][0]
        write_json(a['outcome_path'], {'status': 'completed', 'summary': 'fixture'})
        item['acceptance_commands'][0]['argv'][0] = '/deleted-after-preflight'
        self.engine.store.save('experiments', item['id'], item)
        adapter.states[a['id']] = 'idle'
        self.engine.tick(adapter); self.engine.tick(adapter)
        a = self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(a['status'], 'failed')
        self.assertIn('No such file', a['acceptance']['checks'][0]['execution_error'])
        self.assertEqual(a['business_status'], 'completed')

    def test_successful_checks_do_not_release_slot_until_cleanup_confirmed(self):
        item = self.run_config(); adapter = FakeHerdr(self.engine)
        self.engine.tick(adapter); self.engine.tick(adapter)
        a = item['attempts'][0]
        Path(a['checkout'], 'answer.txt').write_text('done')
        git(a['checkout'], 'add', '.'); git(a['checkout'], 'commit', '-qm', 'done')
        write_json(a['outcome_path'], {'status': 'completed', 'summary': 'fixture'})
        adapter.states[a['id']] = 'idle'
        adapter.cleanup = Mock(return_value=False)
        self.engine.tick(adapter)
        a = self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(a['result_status'], 'completed')
        self.assertEqual(a['status'], 'needs_attention')
        self.assertEqual(len(adapter.created), 2)
        adapter.cleanup.return_value = True
        with patch.object(self.engine, '_accept') as accept:
            self.engine.tick(adapter)
        accept.assert_not_called()
        self.assertEqual(self.engine.get(item['id'])['attempts'][0]['status'], 'completed')
        self.assertEqual(len(adapter.created), 3)

    def test_timeout_keeps_original_error_and_separate_cleanup(self):
        item = self.run_config(); adapter = FakeHerdr(self.engine); self.engine.tick(adapter)
        item = self.engine.get(item['id']); a = item['attempts'][0]
        a.update(status='needs_attention', error='original failure', started_at=time.time()-100000)
        self.engine.store.save('experiments', item['id'], item)
        self.engine.tick(adapter); self.engine.tick(adapter)
        a = self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(a['error'], 'original failure')
        self.assertIn('shutdown', a['shutdown_issue'])
        self.assertEqual(adapter.stops.count(a['id']), 1)

    def test_terminal_read_failure_does_not_disable_registered_deadline_cleanup(self):
        item=self.run_config(); adapter=FakeHerdr(self.engine); self.engine.tick(adapter)
        item=self.engine.get(item['id']);a=item['attempts'][0]
        a.update(started_at=time.time()-100000,cleanup={'registered_supervisor':True})
        self.engine.store.save('experiments',item['id'],item)
        adapter.inspect=Mock(side_effect=RuntimeError('terminal disappeared'))
        adapter.cleanup=Mock(return_value=True)
        self.engine.tick(adapter)
        a=self.engine.get(item['id'])['attempts'][0]
        self.assertEqual(a['status'],'failed');self.assertEqual(a['stop_reason'],'timeout')
        self.assertEqual(adapter.stops,[])

    def test_lane_probe_accepts_own_sibling_directory_rejects_other_group(self):
        a, b = self.run_config()['attempts'][:2]
        lane = Path(a['lanes_dir'])/'worker'; lane.mkdir()
        sid = '11111111-1111-4111-8111-111111111111'
        transcript = self.root / (sid+'.jsonl')
        transcript.write_text(json.dumps({'type':'assistant','sessionId':sid,'cwd':str(lane),'isSidechain':False,
            'message':{'role':'assistant','model':'claude-opus-5-5','content':[], 'stop_reason':'end_turn'}})+'\n')
        state = probe(sid, str(lane), str(transcript), context_path=a['context_path'], context_hash=a['context_hash'])
        self.assertEqual(state['turn_state'], 'complete')
        state = probe(sid, b['lanes_dir'], str(transcript), context_path=a['context_path'], context_hash=a['context_hash'])
        self.assertEqual(state['probe'], 'unavailable')

    def test_real_disk_done_requires_exact_registered_session_and_end_turn(self):
        a = self.run_config()['attempts'][0]
        lane = Path(a['lanes_dir'])/'worker'; (lane/'.dispatch').mkdir(parents=True)
        (lane/'.dispatch/DONE').write_text('synthetic completion')
        sid = '11111111-1111-4111-8111-111111111111'
        record = {'pid':999999,'started':'fixture','session':sid,'cwd':str(lane),'role':'lane',
                  'attempt_id':a['id'],'context_hash':a['context_hash']}
        write_json(Path(a['directory'])/'processes/lane.json',record)
        home = self.root/'synthetic-home'
        from scripts.claude_lane_state import default_transcript
        transcript = default_transcript(home, lane, sid); transcript.parent.mkdir(parents=True)
        row = {'type':'assistant','sessionId':sid,'cwd':str(lane),'isSidechain':False,
               'message':{'role':'assistant','model':'claude-opus-5-5','content':[],'stop_reason':'tool_use'}}
        transcript.write_text(json.dumps(row)+'\n')
        with patch('scripts.claude_lane_state.Path.home',return_value=home):
            self.assertEqual(completed_lanes(a),[])
            row['message']['stop_reason']='end_turn';transcript.write_text(json.dumps(row)+'\n')
            self.assertEqual(len(completed_lanes(a)),1)
            (lane/'.dispatch/DONE').unlink()
            self.assertEqual(completed_lanes(a),[])
            os.utime(transcript,(time.time()-1000,time.time()-1000))
            self.assertEqual(completed_lanes(a)[0]['kind'],'idle_incomplete')

    def test_wrapper_registers_before_exec_without_starting_a_model(self):
        import importlib.util
        from experiments.snapshots import ROOT
        spec=importlib.util.spec_from_file_location('synthetic_launcher',ROOT/'observability/langwatch/instrumentation/launch.py')
        launch=importlib.util.module_from_spec(spec);spec.loader.exec_module(launch)
        a=self.run_config()['attempts'][0];ctx=json.loads(Path(a['context_path']).read_text())
        env={'HERDR_EXPERIMENT_CONTEXT':a['context_path'],'HERDR_EXPERIMENT_CONTEXT_HASH':a['context_hash']}
        argv=['--session-id',a['supervisor_session']]
        with patch.object(launch.sys,'argv',['launch.py','claude',*argv]), \
             patch.object(launch,'prepare',return_value=(sys.executable,argv,env,{},False)), \
             patch('experiments.snapshots.load_context',return_value=ctx), \
             patch('experiments.lifecycle.Path.cwd',return_value=Path(a['checkout'])), \
             patch.object(launch.os,'execvpe') as execute:
            def check_registry(*args):
                self.assertEqual(registrations(a)[0]['session'],a['supervisor_session'])
            execute.side_effect=check_registry
            launch.main()
        execute.assert_called_once()


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root/'lanes').mkdir(); (self.root/'checkout').mkdir()
        self.a = {'id':'att-test','directory':str(self.root),'checkout':str(self.root/'checkout'),
                  'lanes_dir':str(self.root/'lanes'),'context_hash':'fixture-hash',
                  'supervisor_session':'11111111-1111-4111-8111-111111111111'}
        self.r = {'pid':12345,'ppid':1,'pgid':1,'started':'fixed-start','command':'synthetic',
                  'cwd':self.a['checkout'],'role':'supervisor','session':self.a['supervisor_session'],
                  'attempt_id':self.a['id'],'context_hash':self.a['context_hash']}
        write_json(self.root/'processes/root.json', self.r)

    def test_reused_pid_and_foreign_group_are_never_signalled(self):
        with patch('experiments.lifecycle.processes', return_value={12345:self.r|{'started':'reused'}}), patch('os.kill') as kill:
            self.assertTrue(shutdown(self.a)); kill.assert_not_called()
        write_json(self.root/'processes/root.json', self.r|{'attempt_id':'att-foreign'})
        with self.assertRaises(ValueError): registrations(self.a)

    def test_native_descendant_is_retained_after_parent_exit_and_signals_are_bounded(self):
        child = {'pid':12346,'ppid':12345,'started':'child-start','command':'worker','pgid':1}
        table = {12345:self.r,12346:child}
        with patch('experiments.lifecycle.processes', side_effect=lambda: dict(table)), patch('os.kill') as kill:
            track(self.a); table.pop(12345)
            self.assertFalse(shutdown(self.a))
            self.assertFalse(shutdown(self.a)); self.assertEqual(kill.call_count,1)
            rec = next(v for v in self.a['cleanup']['processes'].values() if v['pid']==12346)
            rec['term_attempted_at'] -= 10
            shutdown(self.a); shutdown(self.a)
            self.assertEqual(kill.call_count,2)
            self.assertEqual(kill.call_args.args,(12346,signal.SIGKILL))
            table.clear(); self.assertTrue(shutdown(self.a))

    def test_legacy_registry_absence_is_not_success(self):
        (self.root/'processes/root.json').unlink()
        with patch('experiments.lifecycle.processes', return_value={}), patch('os.kill') as kill:
            self.assertFalse(shutdown(self.a)); kill.assert_not_called()
        self.assertEqual(self.a['cleanup']['status'], 'unverified')

    def test_real_owned_process_group_stops_without_touching_foreign_process(self):
        command = [sys.executable,'-u','-c',
                   'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print(p.pid,flush=True); time.sleep(30)']
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, start_new_session=True)
        foreign = subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(lambda: foreign.poll() is None and foreign.kill())
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        child = int(proc.stdout.readline().strip())
        live = processes()[proc.pid]
        write_json(self.root/'processes/root.json', self.r | live)
        track(self.a); self.assertIn(child, self.a['cleanup']['live_pids'])
        shutdown(self.a)
        proc.wait(timeout=4)
        self.assertIsNone(foreign.poll())
        # Normal scheduler ticks verify exit; this test reaps its own fixture root.
        for _ in range(10):
            if shutdown(self.a): break
            time.sleep(.05)
        self.assertEqual(self.a['cleanup']['status'], 'confirmed')
        foreign.terminate(); foreign.wait(timeout=4); proc.stdout.close()


if __name__ == '__main__': unittest.main()
