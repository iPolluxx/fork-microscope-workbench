"""Render the public, saved attendance evidence. No inference and no fitted recomputation.

Optional figure tooling: uv run --with matplotlib scripts/render_demo_figure.py
"""
from pathlib import Path
import json
import textwrap
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]

def render():
    bundle = json.loads((ROOT/'public/fork-microscope/demo-attendance.json').read_text())
    run = next(r for r in bundle['payload']['runs'] if r['id'] == bundle['manifest']['entry_run_id'])
    p = run['passes'][0]
    curve = p['curve']
    k = run['categories'].index('CHOICE=B')
    record = run['records'][p['id']]
    # Derive counts from the recorded observations, not rounded percentages.
    observations = {t: [o for b in record['branches'] if b['t'] == t for o in b['observations']] for t in curve['positions']}
    counts = [sum(o['label']=='CHOICE=B' for o in observations[t]) for t in curve['positions']]
    totals = [len(observations[t]) for t in curve['positions']]
    assert totals == [20]*17
    assert counts[0] == 12 and counts[-1] == 18
    for i in range(len(counts)):
        assert abs(counts[i]/totals[i]-curve['raw'][i][k]) < 1e-8
    plt.rcParams.update({'font.family':'DejaVu Sans','text.color':'#eaf0f7','axes.labelcolor':'#b8c8dd','xtick.color':'#b8c8dd','ytick.color':'#b8c8dd'})
    fig = plt.figure(figsize=(14,9),facecolor='#0a111c')
    fig.text(.07,.94,'FORK MICROSCOPE',fontsize=13,color='#8fcbf2',weight='bold')
    fig.text(.07,.885,'Same prompt. Different possible answers.',fontsize=26,weight='bold')
    fig.text(.07,.843,'70 guaranteed attendees or a 50/50 chance of 40 or 110? No risk preference specified.',fontsize=12,color='#b8c8dd')
    ax = fig.add_axes([.09,.41,.84,.36],facecolor='#111a28')
    ax.plot(curve['support'],[r[k] for r in curve['smoothed']],color='#8fcbf2',lw=2,label='Saved Goodfire reconstruction')
    ax.scatter(curve['positions'],[a/b for a,b in zip(counts,totals)],color='#efb85f',s=48,zorder=3,label='Recorded frequency · 20 continuations per point')
    ax.set_ylim(0,1.05);ax.set_xlim(125,195);ax.set_ylabel('Fraction of continuations selecting B');ax.set_xlabel('Checkpoint position in the original response (tokens)')
    ax.set_yticks([0,.25,.5,.75,1],['0%','25%','50%','75%','100%']);ax.grid(alpha=.12)
    for spine in ax.spines.values():spine.set_color('#33445f')
    ax.legend(loc='lower right',facecolor='#111a28',edgecolor='#33445f',labelcolor='#eaf0f7',fontsize=10)
    ax.annotate('12 / 20 chose B',xy=(128,.6),xytext=(130,.37),color='#efb85f',arrowprops={'arrowstyle':'->','color':'#efb85f'},fontsize=11)
    ax.annotate('18 / 20 chose B',xy=(192,.9),xytext=(174,.99),color='#efb85f',arrowprops={'arrowstyle':'->','color':'#efb85f'},fontsize=11)
    fig.text(.07,.32,'Two recorded replies from checkpoint 128',fontsize=15,weight='bold')
    for label,x,color in [('CHOICE=A',.07,'#9fd4f3'),('CHOICE=B',.53,'#efb85f')]:
        row=next(o for o in observations[128] if o['label']==label)
        fig.text(x,.277,label,fontsize=12,color=color,weight='bold')
        text=row['reply_text'].replace('\n'+label,'')
        fig.text(x,.247,textwrap.fill(text,64),fontsize=10.5,va='top',linespacing=1.5)
    fig.text(.07,.12,'OBSERVATION, NOT CAUSAL PROOF',fontsize=11,color='#efb85f',weight='bold')
    fig.text(.07,.06,'One saved Muse-Glimmer-30B response · 340 continuations · exploratory interval selection.\nFrequencies are conditional on retained branches. The fitted line is an estimate; no significance claim.',fontsize=10.5,color='#b8c8dd',linespacing=1.5)
    path=ROOT/'docs/images/attendance-evidence.png'
    fig.savefig(path,dpi=150,facecolor=fig.get_facecolor());plt.close(fig)
    print(path)

if __name__=='__main__':render()
