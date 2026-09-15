/* Attribution is shared by every view; saved evidence can name its own revision. */
(() => {
  const tested = 'd32fed8d4162a4888291c4b3a38b059727c85a41';
  const source = 'https://github.com/ericb-goodfire/forking-fast';
  let evidence = null, footer;
  window.setForkMethodCredit = value => { evidence = value; render(); };
  function link(label, href) {const a=document.createElement('a');a.textContent=label;a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;}
  function render() {
    if(!footer)return;
    footer.replaceChildren('Sampling and reconstruction build on ',link('Goodfire’s Forking Fast',source),'. ');
    const runs = evidence === null ? [] : (Array.isArray(evidence) ? evidence : [evidence]);
    const revisions = runs.length ? [...new Set(runs.map(run=>run?.upstream_commit))] : [tested];
    footer.append(runs.length ? 'Recorded upstream revision: ' : 'Tested upstream revision: ');
    revisions.forEach((revision,i)=>{
      if(i)footer.append(' · ');
      if(typeof revision==='string' && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(revision)) {
        const a=link(revision.slice(0,12),source+'/tree/'+revision);a.title=revision;footer.append(a);
      } else footer.append('not recorded');
    });
    footer.append('. Fork Microscope is an independent workbench; attribution does not imply endorsement. ',link('Source code','https://github.com/iPolluxx/fork-microscope-workbench'),' · ',link('Get started','https://github.com/iPolluxx/fork-microscope-workbench/blob/main/docs/GETTING-STARTED.md'));
  }
  function init() {
    footer=document.createElement('footer');footer.id='method-credit';footer.setAttribute('aria-label','Method attribution');
    const style=document.createElement('style');style.textContent='#method-credit{display:block;margin:24px max(16px,3vw);padding:18px 0;border-top:1px solid #34434e;color:#b9c7d2;font:12px/1.7 system-ui;max-width:1400px}#method-credit a{color:#d5e9f7;text-decoration:underline;text-underline-offset:3px}';
    document.head.append(style);document.body.append(footer);render();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
