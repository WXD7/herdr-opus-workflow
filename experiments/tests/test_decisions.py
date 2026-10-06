"""Decision routing regressions, synthetic sessions only; no model calls."""
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch

from experiments.config import DEFAULT_PROFILE, digest, environment
from experiments.decisions import Mailbox, route
from experiments.herdr import Herdr
from experiments.store import write_json
from experiments.tests.test_experiments import Fixture


class DecisionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.item = self.run_config(); self.a, self.b = self.item['attempts'][:2]
        self.a.update(status='running', started_at=time.time(), prompt_attempted=True,
                      target={'session':'fixture-herdr','pane':'parent-pane','name':'parent'})
        self.parent = self.record(self.a, self.a['supervisor_session'], 'supervisor', 123, 'parent-pane', self.a['checkout'])
        lane = Path(self.a['lanes_dir'])/'worker'; lane.mkdir()
        self.child = self.record(self.a, '11111111-1111-4111-8111-111111111111', 'lane', 456, 'lane-pane', str(lane))
        self.mail = Mailbox(self.a)
        self.adapter = Mock()
        self.adapter.decision_target.side_effect = lambda r, a: {'name':r['session']}
        self.adapter.prompt.side_effect = lambda target, prompt, before_submit: before_submit()

    def record(self, a, sid, role, pid, pane, cwd):
        r={'session':sid,'role':role,'pid':pid,'started':'fixture-start','cwd':cwd,
           'pane':pane,'herdr_session':'fixture-herdr','attempt_id':a['id'],'context_hash':a['context_hash'],
           'parent_session':a['supervisor_session'] if role=='lane' else None}
        write_json(Path(a['directory'])/'processes'/f'{pid}.json',r)
        return r

    def request(self, kind='routine'):
        return self.mail.request(self.child,self.item['id'],'copy-label','Use existing success feedback?',kind,time.time()+600)

    def test_default_three_applies_to_snapshot_and_environment(self):
        self.assertEqual(DEFAULT_PROFILE['max_subagents'],3)
        self.assertEqual(environment(self.a['profile'])['CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS'],'3')
        self.assertEqual(self.a['workflow']['decision_transport'],'parent-v1')
        self.assertEqual(len(self.a['workflow']['four_document_sha256']),4)

    def test_request_parent_receipt_answer_child_consumption_roundtrip_once(self):
        q=self.request()
        self.assertTrue(route(self.engine,self.item,self.a,self.adapter))
        self.assertEqual(self.adapter.prompt.call_args[0][0]['name'],self.parent['session'])
        self.assertNotIn('parent_received_at',self.mail.read(q['id']))
        inbox=self.mail.inbox(self.parent);self.assertIn('parent_received_at',inbox[0])
        self.mail.answer(self.parent,q['id'],q['request_hash'],'Reuse existing status feedback')
        route(self.engine,self.item,self.a,self.adapter)
        self.assertEqual(self.adapter.prompt.call_args[0][0]['name'],self.child['session'])
        self.assertNotIn('consumed_at',self.mail.read(q['id']))
        answer=self.mail.consume(self.child,q['id'],q['request_hash'])
        self.assertEqual(answer['answer'],'Reuse existing status feedback')
        self.assertTrue(self.mail.consume(self.child,q['id'],q['request_hash'])['already_consumed'])
        self.assertFalse(route(self.engine,self.item,self.a,self.adapter))
        self.assertEqual(self.adapter.prompt.call_count,2)

    def test_duplicate_and_wrong_request_hash_do_not_create_or_answer(self):
        q=self.request();self.assertEqual(self.request()['id'],q['id'])
        with self.assertRaises(ValueError):
            self.mail.request(self.child,self.item['id'],'copy-label','Changed request','routine',time.time()+600)
        with self.assertRaises(ValueError):
            self.mail.request(self.child,self.item['id'],'new-key','Another request','routine',time.time()+600)
        self.mail.inbox(self.parent)
        with self.assertRaises(ValueError): self.mail.answer(self.parent,q['id'],'wrong','yes')
        with self.assertRaises(ValueError): self.mail.answer(self.child,q['id'],q['request_hash'],'yes')
        self.assertEqual(len(self.mail.all()),1)

    def test_foreign_group_tamper_expired_and_replaced_answer_rejected(self):
        q=self.request();self.mail.inbox(self.parent)
        with self.assertRaises(ValueError): Mailbox(self.b).validate(q)
        altered=q|{'question':'changed'}
        with self.assertRaises(ValueError): self.mail.validate(altered)
        self.mail.answer(self.parent,q['id'],q['request_hash'],'yes')
        with self.assertRaises(ValueError): self.mail.answer(self.parent,q['id'],q['request_hash'],'no')
        with patch('experiments.decisions.time.time',return_value=q['expires_at']+1):
            with self.assertRaises(ValueError): self.mail.consume(self.child,q['id'],q['request_hash'])
            route(self.engine,self.item,self.a,self.adapter)
        self.adapter.prompt.assert_not_called()

    def test_uncertain_send_is_durable_not_repeated_on_new_mailbox(self):
        q=self.request()
        def uncertain(target,prompt,before_submit): before_submit();raise RuntimeError('timed out')
        self.adapter.prompt.side_effect=uncertain
        route(self.engine,self.item,self.a,self.adapter)
        route(self.engine,self.item,self.a,self.adapter)
        self.adapter.prompt.assert_called_once()
        self.assertEqual(Mailbox(self.a).read(q['id'])['delivery']['parent']['status'],'uncertain')

    def test_busy_recipient_waits_without_model_call_then_delivers(self):
        self.request();self.adapter.decision_target.return_value=None
        self.adapter.decision_target.side_effect=None
        route(self.engine,self.item,self.a,self.adapter);self.adapter.prompt.assert_not_called()
        self.adapter.decision_target.return_value={'name':self.parent['session']}
        route(self.engine,self.item,self.a,self.adapter);self.adapter.prompt.assert_called_once()

    def test_draft_or_native_dialog_failure_visible_without_retry(self):
        q=self.request();self.adapter.prompt.side_effect=ValueError('Input draft preserved')
        route(self.engine,self.item,self.a,self.adapter);route(self.engine,self.item,self.a,self.adapter)
        self.adapter.prompt.assert_called_once()
        self.assertEqual(self.mail.read(q['id'])['delivery']['parent']['status'],'not_submitted')
        self.assertTrue(any(e['name']=='attempt.blocked' for e in self.engine.store.list('events')))

    def test_authority_not_autoapproved_and_completion_not_accepted(self):
        q=self.request('authority');self.mail.inbox(self.parent)
        with self.assertRaises(ValueError): self.mail.answer(self.parent,q['id'],q['request_hash'],'yes')
        write_json(self.a['outcome_path'],{'status':'completed'})
        adapter=Mock();adapter.session='fixture-herdr';adapter.inspect.return_value={'status':'idle'}
        with patch.object(self.engine,'_accept') as accept:
            self.engine._observe(self.item,self.a,adapter)
        accept.assert_not_called();self.assertEqual(self.a['status'],'needs_attention')

    def test_actor_uses_process_ancestry_not_an_arbitrary_session_parameter(self):
        table={999:{'ppid':456},456:{'ppid':1,'started':'fixture-start'}}
        with patch('experiments.decisions.current_session'):
            self.assertEqual(self.mail.actor(table,999)['session'],self.child['session'])
        table[456]['started']='reused-pid'
        with self.assertRaises(ValueError):self.mail.actor(table,999)

    def test_exact_target_checks_process_pane_session_and_transcript(self):
        adapter=Herdr.__new__(Herdr);adapter.session='fixture-herdr'
        adapter.call=Mock(return_value={'name':'parent','pane_id':'parent-pane','status':'idle'})
        with patch('experiments.lifecycle.processes',return_value={123:{'started':'fixture-start'}}), \
             patch('experiments.lifecycle.current_session'), \
             patch('scripts.claude_lane_state.probe',return_value={'probe':'ok','turn_state':'complete'}):
            target=adapter.decision_target(self.parent,self.a)
            self.assertEqual(target['name'],'parent')
            adapter.call.return_value={'name':'another-parent','pane_id':'parent-pane','status':'idle'}
            with self.assertRaises(ValueError):adapter.decision_target(self.parent,self.a)
        with patch('experiments.lifecycle.processes',return_value={123:{'started':'reused'}}):
            with self.assertRaises(ValueError):adapter.decision_target(self.parent,self.a)

    def test_new_session_inside_same_pid_is_rejected(self):
        from experiments.lifecycle import current_session
        path=self.root/'.claude/sessions'/f"{self.child['pid']}.json"
        write_json(path,{'pid':self.child['pid'],'sessionId':self.child['session'],'cwd':self.child['cwd']})
        with patch('experiments.lifecycle.Path.home',return_value=self.root):
            current_session(self.child)
            write_json(path,{'pid':self.child['pid'],'sessionId':self.parent['session'],'cwd':self.child['cwd']})
            with self.assertRaisesRegex(ValueError,'changed'):current_session(self.child)

    def test_stopping_group_never_delivers_decision(self):
        q=self.request();self.a['stop_attempted']=True
        with self.assertRaises(ValueError):self.mail.consume(self.child,q['id'],q['request_hash'])

    def test_unresolved_decision_cannot_disable_timeout_cleanup(self):
        self.request();write_json(self.a['outcome_path'],{'status':'completed'})
        self.a['started_at']=time.time()-self.item['config']['timeout_minutes']*60-1
        import os
        os.utime(self.a['outcome_path'],(self.a['started_at']+1,self.a['started_at']+1))
        adapter=Mock();adapter.session='fixture-herdr';adapter.inspect.return_value={'status':'idle'}
        adapter.cleanup.return_value=True
        self.engine._observe(self.item,self.a,adapter)
        adapter.cleanup.assert_called_once();self.assertEqual(self.a['status'],'failed')

    def test_adapter_defaults_do_not_change_historical_fixed_limits(self):
        # Modern profiles are configurable; historical rules stay frozen, not silently rewritten.
        from experiments.config import profile
        self.assertEqual(profile({'max_subagents':20})['max_subagents'],20)
        self.assertEqual(profile({})['max_subagents'],3)
