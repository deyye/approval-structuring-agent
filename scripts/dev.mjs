// Optional npm launcher for supervised browser preview; Python remains the runtime.
import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
const args=process.argv.slice(2).filter(x=>x!=='--strictPort');
const p=spawn(process.env.PYTHON||(existsSync('.venv/bin/python')?'.venv/bin/python':'python3'),['-m','app.server',...args],{stdio:'inherit',env:{...process.env,DATA_DIR:process.env.DATA_DIR||'data'}});
p.on('error',e=>{console.error(e.message);process.exitCode=1});
p.on('exit',code=>process.exit(code??1));
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>p.kill(signal));
