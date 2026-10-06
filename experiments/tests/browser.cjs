// Real UI/API smoke test. Uses a temporary Git fixture, no Herdr process or model.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
(async()=>{
 const root=path.resolve(__dirname,'../..'), tmp=fs.mkdtempSync(path.join(os.tmpdir(),'herdr-ui-'));
 const repo=path.join(tmp,'repo'),state=path.join(tmp,'state');fs.mkdirSync(repo);
 const git=(...args)=>execFileSync('git',['-C',repo,...args],{stdio:'pipe'});
 git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid');
 fs.writeFileSync(path.join(repo,'README.md'),'Synthetic UI fixture');git('add','.');git('commit','-qm','fixture');
 const server=spawn(process.env.TEST_PYTHON||'python3.13',[path.join(root,'scripts/experiment.py'),'--state-dir',state,'--allow-repo',repo,'serve'],{stdio:['ignore','pipe','pipe']});
 let browser;
 try{
  const info=await new Promise((resolve,reject)=>{let raw='';const timer=setTimeout(()=>reject(Error('Server startup timeout')),10000);server.stdout.on('data',b=>{raw+=b; if(raw.includes('\n')){clearTimeout(timer);resolve(JSON.parse(raw.split('\n')[0]));}});server.once('exit',code=>{clearTimeout(timer);reject(Error('Server exited '+code));});});
  browser=await chromium.launch({headless:true,...(process.env.TEST_CHROME?{executablePath:process.env.TEST_CHROME}:{})});
  const page=await browser.newPage({viewport:{width:1280,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(info.url);await page.locator('#token').fill(fs.readFileSync(info.token_file,'utf8').trim());await page.locator('#connect').click();
  await page.locator('#connection').filter({hasText:'配置服务就绪'}).waitFor();
  await page.locator('#title').fill('多配置隔离实验 · UI 合成测试');await page.locator('#task').fill('各组独立完善同一份示例业务逻辑');await page.locator('#acceptance').fill('所有组使用相同起点和标准');
  await page.locator('#add').click();await page.locator('#add').click();assert.equal(await page.locator('.group').count(),3);
  await page.getByLabel('B 思考强度',{exact:true}).selectOption('medium');
  await page.getByLabel('C 模型',{exact:true}).selectOption('claude-fable-5');
  await page.locator('#parallel').fill('2');await page.locator('#save').click();await page.locator('#notice').filter({hasText:'草稿已保存'}).waitFor();
  await page.locator('#instruction').fill('复制 A 为 D；D 用 Fable 5 medium');await page.locator('#apply').click();await page.locator('.group').nth(3).waitFor();
  assert.equal(await page.getByLabel('D 模型',{exact:true}).inputValue(),'claude-fable-5');
  await page.getByRole('button',{name:'移除 D 组',exact:true}).click();assert.equal(await page.locator('.group').count(),3);
  const out=process.env.TEST_OUTPUT||path.join(root,'evidence/experiment-ui');fs.mkdirSync(out,{recursive:true});
  for(const width of [1280,768,360]){await page.setViewportSize({width,height:1000});await page.screenshot({path:path.join(out,'ui-'+width+'.png'),fullPage:width>700});if(width===360){await page.locator('#groups').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'ui-360-groups.png')});}assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Horizontal overflow at '+width);}
  await page.locator('#start').click();await page.locator('#run-status').filter({hasText:'排队'}).waitFor({timeout:20000});assert.equal(await page.locator('.result').count(),3);
  assert.equal(await page.locator('#title').isDisabled(),true);assert.match(await page.locator('#connection').textContent(),/Herdr 未连接/);
  await page.locator('#stop').click();await page.locator('#results').filter({hasText:'已取消'}).waitFor();
  await page.locator('#clone').click();await page.waitForFunction(()=>!document.querySelector('#title').disabled);assert.equal(await page.locator('#title').isDisabled(),false);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,viewports:[1280,768,360],groups:3,model_calls:0,screenshots:out}));
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
