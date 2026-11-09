import { Browser } from 'file:///C:/Users/11824/.codex/skills/taste-skill/scripts/lib/cdp.mjs';
import { mkdirSync,writeFileSync,readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const root=resolve('presentations/smart-migrate');
const review=`${root}/concepts/review/confirmed`;
mkdirSync(review,{recursive:true});
const results=[];
const browser=await Browser.launch();
try{
 const page=await browser.newPage();
 for(const [width,height] of [[1280,800],[1440,900],[1920,1080]]){
  await page.setViewport({width,height});
  await page.goto(pathToFileURL(`${root}/index.html`).href,{settleMs:100});
  await page.eval(()=>document.fonts.ready);
  const shownSamples=await page.eval(()=>[...document.querySelectorAll('.sample-json')].map(p=>JSON.parse(p.textContent)));
  const sampleNames=['NEXT_MIG_INFO','NEXT_MIG_INFO_DTL','NEXT_SQL_INFO'];
  if(shownSamples.length!==sampleNames.length)throw Error('Missing complete JSON samples');
  for(let i=0;i<sampleNames.length;i++){
   const expected=JSON.parse(readFileSync(resolve(`smartMigrate_3.0_Dify/samples/tables/${sampleNames[i]}.json`),'utf8'));
   if(JSON.stringify(shownSamples[i])!==JSON.stringify(expected))throw Error(`Displayed sample differs: ${sampleNames[i]}`);
  }
  const downloads=await page.eval(()=>[...document.querySelectorAll('.sample-download')].map(a=>a.href));
  for(let i=0;i<downloads.length;i++){
   if(JSON.stringify(JSON.parse(readFileSync(new URL(downloads[i]),'utf8')))!==JSON.stringify(shownSamples[i]))throw Error('Sample download differs from displayed JSON');
  }
  const metrics=await page.eval(()=>({width:innerWidth,height:innerHeight,pageHeight:document.documentElement.scrollHeight,
   overflow:document.documentElement.scrollWidth>innerWidth+1,fontLoaded:document.fonts.check('18px Pretendard'),
   sections:[...document.querySelectorAll('section')].map(s=>s.id),
   missingAnchors:[...document.querySelectorAll('a[href^="#"]')].filter(a=>!document.getElementById(a.hash.slice(1))||new URL(a.href).pathname!==location.pathname).map(a=>a.hash),
   clipped:[...document.querySelectorAll('h1,h2,h3,pre,button')].filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>e.textContent.trim().slice(0,60)),
   removedConceptLinks:[...document.links].filter(a=>/b-architecture|c-story/.test(a.href)).length
  }));
  if(metrics.overflow||!metrics.fontLoaded||metrics.missingAnchors.length||metrics.clipped.length||metrics.sections.length!==16||metrics.removedConceptLinks)throw Error(JSON.stringify(metrics));
  results.push(metrics);
  await page.screenshot({path:`${review}/hero-${width}.png`,format:'png'});
  for(const id of metrics.sections){
   await page.eval(id=>document.getElementById(id).scrollIntoView({block:'start'}),id);
   const sectionMetrics=await page.eval(id=>{const r=document.getElementById(id).getBoundingClientRect();return {top:r.top,height:r.height};},id);
   if(width===1440){
    await page.screenshot({path:`${review}/${id}.png`,format:'png'});
    if(sectionMetrics.height>height){
     for(let offset=height-150;offset<sectionMetrics.height;offset+=height-150){
      await page.eval(({id,offset})=>{const e=document.getElementById(id);window.scrollTo(0,e.offsetTop+offset-108);},{id,offset});
      await page.screenshot({path:`${review}/${id}-${offset}.png`,format:'png'});
     }
    }
   }
  }
  for(const agent of ['migration','conversion','tuning','formatting']){
   await page.eval(agent=>document.querySelector(`[data-agent="${agent}"]`).click(),agent);
   if(!await page.eval(agent=>document.querySelector(`[data-agent="${agent}"]`).getAttribute('aria-selected')==='true',agent))throw Error('Agent selection failed');
  }
  await page.eval(()=>document.querySelector('[data-agent="migration"]').focus());
  await page.send('Input.dispatchKeyEvent',{type:'keyDown',key:'End',code:'End',windowsVirtualKeyCode:35});
  if(!await page.eval(()=>document.querySelector('[data-agent="formatting"]').getAttribute('aria-selected')==='true'))throw Error('Keyboard tabs failed');
  await page.eval(()=>document.querySelectorAll('details').forEach(d=>d.open=true));
  if(!await page.eval(()=>[...document.querySelectorAll('details')].every(d=>d.open)))throw Error('Details failed');
  const expanded=await page.eval(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,details:document.querySelectorAll('details').length}));
  if(expanded.overflow)throw Error('Expanded details overflow');
 }
 for(const file of ['SmartMigrate-Agent.html','concepts/a-report.html']){
  await page.goto(pathToFileURL(`${root}/${file}`).href,{settleMs:100});
  if(!await page.eval(()=>document.querySelectorAll('section').length===16))throw Error(`Entry failed ${file}`);
 }
 if(page.consoleErrors.length)throw Error(page.consoleErrors.join('\n'));
 writeFileSync(`${review}/browser-checks.json`,JSON.stringify({results,controls:'anchors, agent click/keyboard, expanded details, all entries',consoleErrors:page.consoleErrors},null,2));
 console.log(JSON.stringify({desktopWidths:[1280,1440,1920],sections:16,controls:'PASS',overflow:0,consoleErrors:0}));
}finally{await browser.close();}
