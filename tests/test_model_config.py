"""界面可改模型配置：即时生效、密钥不回显、非法值不落盘。"""
import json,os,tempfile,threading,unittest,urllib.error,urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from app.server import Handler,Store
from app.model_client import ModelError,clear_config,public_config,save_config,set_config_path,settings

# 以一份「环境变量里已经配好」的基线开场：这样才能证明界面保存的值确实盖过了它，
# 而不是因为环境变量本来就是空的才碰巧生效。
BASE_ENV={'LLM_BASE_URL':'https://env.example/v1','LLM_API_KEY':'sk-env-secret-000001','LLM_MODEL':'env-model'}


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'model_config.json'
        set_config_path(self.path);self.addCleanup(set_config_path,None)
        self.env=patch.dict(os.environ,dict(BASE_ENV),clear=True);self.env.start();self.addCleanup(self.env.stop)

    def test_env_is_used_until_something_is_saved(self):
        self.assertEqual(public_config()['source'],'env')
        self.assertTrue(public_config()['llm_ready'])
        endpoint,key,*_=settings()
        self.assertEqual(endpoint,'https://env.example/v1/chat/completions')
        self.assertEqual(key,'sk-env-secret-000001')

    def test_saved_config_overrides_env_and_applies_without_restart(self):
        saved=save_config({'LLM_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1',
                           'LLM_API_KEY':'sk-dash-abcdefgh','LLM_MODEL':'qwen-plus'})
        self.assertTrue(saved['llm_ready']);self.assertEqual(saved['source'],'runtime')
        self.assertEqual(settings()[0],'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
        self.assertEqual(settings()[1],'sk-dash-abcdefgh')
        # 换厂商在同一进程内直接生效，这正是"模型随时可替换"的那条路径
        save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1','LLM_MODEL':'deepseek-chat'})
        self.assertEqual(public_config()['model'],'deepseek-chat')
        self.assertEqual(settings()[0],'https://api.deepseek.com/v1/chat/completions')

    def test_secret_never_returned_by_public_config(self):
        saved=save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1',
                           'LLM_API_KEY':'sk-verysecretvalue123','LLM_MODEL':'deepseek-chat'})
        self.assertNotIn('sk-verysecretvalue123',json.dumps(saved,ensure_ascii=False))
        self.assertTrue(saved['has_key']);self.assertEqual(saved['key_hint'],'····e123')

    def test_blank_key_keeps_the_stored_one_and_clear_removes_it(self):
        save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1',
                     'LLM_API_KEY':'sk-keepme-abcdefg','LLM_MODEL':'deepseek-chat'})
        saved=save_config({'LLM_MODEL':'deepseek-reasoner','LLM_API_KEY':''})
        self.assertTrue(saved['has_key']);self.assertEqual(saved['model'],'deepseek-reasoner')
        self.assertEqual(settings()[1],'sk-keepme-abcdefg')
        # 显式清除后不再回落 .env：否则用户以为清掉了，实际还在用旧密钥
        cleared=save_config({},clear_key=True)
        self.assertFalse(cleared['has_key'])
        with self.assertRaises(ModelError):settings()

    def test_invalid_config_is_rejected_without_touching_the_working_one(self):
        save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1',
                     'LLM_API_KEY':'sk-good-key-000001','LLM_MODEL':'deepseek-chat'})
        before=self.path.read_text(encoding='utf-8')
        for bad in [{'LLM_BASE_URL':'http://evil.example.com/v1'},
                    {'LLM_BASE_URL':'https://user:pw@host/v1'},
                    {'LLM_BASE_URL':'https://api.deepseek.com/v1?x=1'},
                    {'LLM_JSON_MODE':'auto'}]:
            with self.subTest(bad=bad),self.assertRaises(ModelError):save_config(bad)
        self.assertEqual(self.path.read_text(encoding='utf-8'),before)
        self.assertEqual(public_config()['model'],'deepseek-chat')

    def test_blank_model_name_keeps_the_panel_disabled(self):
        saved=save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1',
                           'LLM_API_KEY':'sk-abcdefghijklm','LLM_MODEL':''})
        self.assertFalse(saved['llm_ready']);self.assertIn('LLM_MODEL',saved['model_error'])

    def test_partial_config_saves_then_completes(self):
        """先填地址和模型名、密钥后补：不能因为缺密钥就把前半截也存不下。"""
        os.environ.pop('LLM_API_KEY',None)
        partial=save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1','LLM_MODEL':'deepseek-chat'})
        self.assertFalse(partial['llm_ready'])
        self.assertIn('LLM_API_KEY',partial['model_error'])
        self.assertTrue(self.path.exists())
        completed=save_config({'LLM_API_KEY':'sk-later-added-0001'})
        self.assertTrue(completed['llm_ready'])
        self.assertEqual(settings()[1],'sk-later-added-0001')

    def test_clear_config_falls_back_to_env(self):
        save_config({'LLM_BASE_URL':'https://api.deepseek.com/v1',
                     'LLM_API_KEY':'sk-x-abcdefghij','LLM_MODEL':'deepseek-chat'})
        self.assertEqual(public_config()['source'],'runtime')
        restored=clear_config()
        self.assertEqual(restored['source'],'env');self.assertFalse(self.path.exists())
        self.assertEqual(restored['model'],'env-model');self.assertEqual(settings()[1],'sk-env-secret-000001')

    def test_http_endpoints_save_reset_and_reject_bad_input(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ThreadingHTTPServer(('127.0.0.1',0),Handler);service.store=Store(directory)
            worker=threading.Thread(target=service.serve_forever,daemon=True);worker.start()
            base=f'http://127.0.0.1:{service.server_port}'
            def call(path,data=None,header=True):
                headers={'Content-Type':'application/json'}
                if header:headers['X-Requested-With']='ApprovalAgent'
                request=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
                return urllib.request.urlopen(request)
            try:
                with call('/api/config') as r:self.assertEqual(json.load(r)['source'],'env')
                with call('/api/model/config',{'values':{'LLM_BASE_URL':'https://api.deepseek.com/v1',
                        'LLM_API_KEY':'sk-http-secret-0001','LLM_MODEL':'deepseek-chat'}}) as r:saved=json.load(r)
                self.assertTrue(saved['llm_ready'])
                self.assertNotIn('sk-http-secret-0001',json.dumps(saved,ensure_ascii=False))
                with call('/api/config') as r:after=json.load(r)
                self.assertEqual(after['source'],'runtime');self.assertEqual(after['model'],'deepseek-chat')
                with self.assertRaises(urllib.error.HTTPError) as err:
                    call('/api/model/config',{'values':{'LLM_BASE_URL':'http://evil.example.com/v1'}})
                self.assertEqual(err.exception.code,400)
                self.assertEqual(public_config()['model'],'deepseek-chat')
                with call('/api/model/config/reset',{}) as r:restored=json.load(r)
                self.assertEqual(restored['source'],'env')
                with self.assertRaises(urllib.error.HTTPError) as err:call('/api/model/config',{'values':{}},header=False)
                self.assertEqual(err.exception.code,403)
            finally:
                service.shutdown();service.server_close();service.store.executor.shutdown();worker.join()


if __name__=='__main__':unittest.main()
