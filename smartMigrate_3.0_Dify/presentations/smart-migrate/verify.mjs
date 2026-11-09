// Local browser QA; uses the installed Taste Skill CDP helper, no npm packages.
import { Browser } from 'file:///C:/Users/11824/.codex/skills/taste-skill/scripts/lib/cdp.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const root = resolve('presentations/smart-migrate');
mkdirSync(`${root}/review`, {recursive:true});
const browser = await Browser.launch();
const results = [];
try {
  const page = await browser.newPage();
  for (const [width, height] of [[1440,900],[768,1024],[390,844]]) {
    await page.setViewport({width,height});
    await page.goto(pathToFileURL(`${root}/index.html`).href, {settleMs:100});
    for (let n = 1; n <= 11; n++) {
      await page.eval(n => { location.hash = `slide-${n}`; }, n);
      await page.eval(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const metrics = await page.eval(() => {
        const slide = document.querySelector('.slide.active');
        const r = slide.getBoundingClientRect();
        return {id:slide.id,visible:!!r.height,horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,contentBottom:r.bottom,viewport:innerHeight};
      });
      results.push({width,n,...metrics});
      await page.screenshot({path:`${root}/review/slide-${n}-${width}.png`,format:'png',fullPage:true});
    }
  }
  await page.eval(() => {document.getElementById('prev').click();});
  if (await page.eval(() => location.hash) !== '#slide-10') throw Error('Previous control failed');
  await page.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowLeft',code:'ArrowLeft',windowsVirtualKeyCode:37});
  await page.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowLeft',code:'ArrowLeft',windowsVirtualKeyCode:37});
  if (await page.eval(() => location.hash) !== '#slide-9') throw Error('Keyboard navigation failed');
  await page.eval(() => document.getElementById('notes').click());
  if (!await page.eval(() => getComputedStyle(document.querySelector('.active .note')).display !== 'none')) throw Error('Notes failed');
  await page.eval(() => document.getElementById('overview').click());
  if (await page.eval(() => [...document.querySelectorAll('.slide')].filter(s => getComputedStyle(s).display !== 'none').length) !== 11) throw Error('Overview failed');
  await page.eval(() => {document.getElementById('overview').click();document.getElementById('notes').click();});
  await page.send('Emulation.setEmulatedMedia',{media:'print'});
  if (await page.eval(() => [...document.querySelectorAll('.slide')].filter(s => getComputedStyle(s).display !== 'none').length) !== 11) throw Error('Print visibility failed');
  const pdf = await page.send('Page.printToPDF',{landscape:true,printBackground:true,preferCSSPageSize:true});
  writeFileSync(`${root}/SmartMigrate-Agent.pdf`,Buffer.from(pdf.data,'base64'));
  if (page.consoleErrors.length) throw Error(page.consoleErrors.join('\n'));
  writeFileSync(`${root}/review/browser-checks.json`,JSON.stringify({results,controls:'previous, keyboard, notes, overview, print passed',consoleErrors:page.consoleErrors},null,2));
  console.log(JSON.stringify({slides:11,viewports:3,horizontalOverflow:results.filter(r=>r.horizontalOverflow),controls:'PASS',pdf:'SmartMigrate-Agent.pdf'}));
} finally { await browser.close(); }
