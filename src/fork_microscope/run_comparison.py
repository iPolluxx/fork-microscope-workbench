"""Compare saved same-trace sampling passes. No fitting, generation, or model import."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re

from fork_microscope.model_preflight import same_model_identity

SCHEMA = 'run_comparison.v1'
DESIGN = 'position_mixture_v1'
CANDIDATE_TOLERANCE = 1e-5


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _ids(value):
    return type(value) is list and bool(value) and all(type(x) is int and x >= 0 for x in value)


def _pass(run, pass_id):
    record = run.get('records', {}).get(pass_id)
    if not isinstance(record, dict):
        raise ValueError(f'No saved observation record for pass {pass_id}.')
    chosen = next((p for p in run.get('passes', []) if p.get('id') == pass_id), None)
    if chosen is None and pass_id == 'dense' and run.get('reference') is not None:
        chosen = {'id': 'dense', 'label': 'Every-token reference', 'curve': {}}
    if chosen is None:
        raise ValueError(f'Unknown completed pass {pass_id}.')
    return chosen, record


def _evidence(run, pass_id):
    chosen, rec = _pass(run, pass_id)
    categories = run.get('categories')
    if not isinstance(categories, list) or not categories or any(not isinstance(c, str) for c in categories) or len(set(categories)) != len(categories):
        raise ValueError('Outcome categories are missing or invalid.')
    if rec.get('categories') != categories:
        raise ValueError('Record and result outcome categories disagree.')
    if rec.get('sampling_design') != DESIGN or run.get('sampling_design') != DESIGN:
        raise ValueError('Only verified position_mixture_v1 observations are supported; legacy weighted records need a separate comparison method.')
    base = rec.get('base', {})
    if not _ids(base.get('prompt_ids')) or not _ids(base.get('gen_ids')):
        raise ValueError('Exact prompt and original-response token IDs are required.')
    positions = rec.get('positions', [])
    if not positions:
        raise ValueError('This pass contains no checkpoint observations.')
    by_position = {}
    candidate_distributions = {}
    for row in positions:
        t, n = row.get('t'), row.get('samples')
        if type(t) is not int or not 0 <= t < len(base['gen_ids']) or t in by_position or type(n) is not int or n < 1:
            raise ValueError('Checkpoint positions or draw counts are invalid.')
        mass = row.get('retained_mass')
        if not _number(mass) or not 0 < mass <= 1 + 1e-6:
            raise ValueError('Retained next-token mass is missing or invalid.')
        candidates = row.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f'Candidate token probabilities are missing at checkpoint {t}; mixture identity is unverified.')
        probabilities = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError(f'Invalid candidate metadata at checkpoint {t}.')
            token, probability = candidate.get('tok_id'), candidate.get('tok_p')
            if type(token) is not int or token < 0 or token in probabilities or not _number(probability) or not 0 < probability <= 1:
                raise ValueError(f'Invalid candidate token probabilities at checkpoint {t}.')
            probabilities[token] = probability
        if abs(sum(probabilities.values())-mass) > CANDIDATE_TOLERANCE:
            raise ValueError(f'Candidate probabilities disagree with retained mass at checkpoint {t}.')
        candidate_distributions[t] = probabilities
        by_position[t] = {'t': t, 'samples': n, 'retained_mass': min(1., mass), 'draws': {}}
    observations = []
    observation_metadata_complete = True
    rng_selection, rng_generation = set(), set()
    rng_complete = True
    for row in positions:
        seed = row.get('selection_seed')
        if not isinstance(seed, list) or not seed or any(type(x) is not int or x < 0 for x in seed):
            rng_complete = False
        else:
            rng_selection.add(tuple(seed))
    batch = run.get('model', {}).get('batch_size')
    if type(batch) is not int or batch < 1:
        rng_complete = False
    tokens = 0
    tokens_complete = True
    for branch in rec.get('branches', []):
        t = branch.get('t')
        if t not in by_position:
            raise ValueError('A continuation refers to an unrecorded checkpoint.')
        if branch.get('tok_id') not in candidate_distributions[t]:
            raise ValueError('A continuation token is absent from the recorded candidate distribution.')
        answers, indices = branch.get('answers'), branch.get('draw_indices')
        if not isinstance(answers, list) or not isinstance(indices, list) or len(answers) != len(indices):
            raise ValueError('Continuation labels and draw indices disagree.')
        for index, label in zip(indices, answers):
            if type(index) is not int or index < 0 or index in by_position[t]['draws'] or label not in categories:
                raise ValueError('Duplicate/invalid draw index or unknown outcome label.')
            by_position[t]['draws'][index] = label
        obs = branch.get('observations')
        if not isinstance(obs, list) or len(obs) != len(answers) or any(not isinstance(o, dict) for o in obs):
            observation_metadata_complete = False
        else:
            if any(o.get('label') != label for o, label in zip(obs, answers)):
                raise ValueError('Observation and stored answer labels disagree.')
            observations.extend(obs)
        lengths = branch.get('cont_lens')
        if not isinstance(lengths, list) or len(lengths) != len(answers) or any(type(n) is not int or n < 0 for n in lengths):
            tokens_complete = False
        else:
            tokens += sum(lengths)
        seed = branch.get('seed')
        if type(seed) is not int or seed < 0 or type(batch) is not int or batch < 1:
            rng_complete = False
        else:
            # AttachedModel.draw_branch records seed + batch offset modulo this bound.
            rng_generation.update((seed + lo) % (2**31 - 1) for lo in range(0, len(answers), batch))
    rows = []
    for t in sorted(by_position):
        row = by_position[t]
        if set(row['draws']) != set(range(row['samples'])):
            raise ValueError('A checkpoint is incomplete; every draw must appear exactly once.')
        counts = [sum(label == c for label in row['draws'].values()) for c in categories]
        rows.append({k: row[k] for k in ['t', 'samples', 'retained_mass']} | {'counts': counts, 'values': [c/row['samples'] for c in counts]})
    curve = chosen.get('curve', {})
    fits, fit_note = [], None
    if curve.get('parameters') and curve.get('fit_status') != 'withheld':
        actual = {r['t']: r['values'] for r in rows}
        saved = curve.get('weighted', [])
        cpos = curve.get('positions', [])
        if cpos != [r['t'] for r in rows] or len(saved) != len(rows) or any(not _vector(v, len(categories)) or any(abs(a-b) > 1e-6 for a, b in zip(v, actual[t])) for t, v in zip(cpos, saved)):
            fit_note = 'Saved fit input proportions do not agree with verified draws; fit excluded.'
        else:
            seen = set()
            for t, values in zip(curve.get('support', []), curve.get('smoothed', [])):
                if type(t) is int and rows[0]['t'] <= t <= rows[-1]['t'] and t not in seen and _vector(values, len(categories)):
                    fits.append({'t': t, 'values': values}); seen.add(t)
            fits.sort(key=lambda r: r['t'])
            if len(fits) != len(curve.get('support', [])) or len(curve.get('support', [])) != len(curve.get('smoothed', [])):
                fit_note = 'Some malformed, duplicate or out-of-range saved fit points were excluded.'
    elif pass_id != 'dense':
        fit_note = 'No supported saved fit for this pass; raw observations remain available.'
    total = sum(r['samples'] for r in rows)
    measured = run.get('measured', {}).get(pass_id, {})
    wall = measured.get('wall_seconds')
    cap = sum(o.get('stop_reason') == 'length' for o in observations) if observation_metadata_complete and all(o.get('stop_reason') in ('length', 'eos') for o in observations) else None
    other = sum(r['counts'][categories.index('Other')] for r in rows) if 'Other' in categories else 0
    summary = {'run_id': run.get('id'), 'pass_id': pass_id, 'label': chosen.get('label', pass_id),
               'model_id': run.get('model', {}).get('model_id'), 'revision': run.get('model', {}).get('resolved_revision'),
               'checkpoints': len(rows), 'range': [rows[0]['t'], rows[-1]['t']],
               'draws': total, 'draws_per_checkpoint': {'min': min(r['samples'] for r in rows), 'max': max(r['samples'] for r in rows)},
               'generated_tokens': tokens if tokens_complete else None, 'collection_seconds': wall if _number(wall) and wall >= 0 else None,
               'cap_hits': cap, 'cap_fraction': cap/total if cap is not None else None,
               'other_count': other, 'other_fraction': other/total,
               'retained_mass': {'min': min(r['retained_mass'] for r in rows), 'max': max(r['retained_mass'] for r in rows)},
               'fit_note': fit_note, 'observed': rows, 'fit': fits}
    return {'run': run, 'pass': chosen, 'record': rec, 'summary': summary, 'selection_seeds': rng_selection,
            'generation_seeds': rng_generation, 'rng_complete': rng_complete,
            'candidate_distributions': candidate_distributions,
            'evidence_hash': _digest({'positions': positions, 'branches': rec.get('branches', [])})}


def _vector(value, k):
    return isinstance(value, list) and len(value) == k and all(_number(v) and 0 <= v <= 1 for v in value) and abs(sum(value)-1) < 1e-6


def _tvd(a, b):
    return sum(abs(x-y) for x, y in zip(a, b))/2


def _settings(item):
    run, rec = item['run'], item['record']
    settings, config = run.get('settings', {}), rec.get('config', {})
    pairs = [('temperature', 'cont_temperature'), ('top_k', 'top_k'), ('threshold', 'p_thresh'), ('cont_max', 'cont_max_tokens')]
    for top, local in pairs:
        if top not in settings or local not in config or not _number(settings[top]) or settings[top] != config[local]:
            raise ValueError(f'Missing or inconsistent recorded {top}.')
    return {k: settings[k] for k, _ in pairs} | {'effective_sampling': run.get('effective_sampling')}


def _parser(run):
    question = run.get('base', {}).get('question', {})
    config = run.get('base_config', {})
    # Exact recorded prompt and parser identity; tracking labels are not semantic truth.
    if not question.get('matching'):
        raise ValueError('The parser version is not recorded; parser equivalence is unverified.')
    return {'question': question, 'prompt': config.get('prompt', config.get('question')), 'mode': config.get('mode'),
            'answers': config.get('answers'), 'choices': config.get('choices')}


def _direction(fit_side, reference_side):
    fit, reference = fit_side['summary'], reference_side['summary']
    by_t = {r['t']: r for r in fit['fit']}
    train = {r['t'] for r in fit['observed']}
    excluded = {'training_checkpoint_overlap': [], 'outside_fit_range': [], 'missing_saved_fit_point': []}
    rows = []
    for ref in reference['observed']:
        t = ref['t']
        if t in train:
            excluded['training_checkpoint_overlap'].append(t)
        elif not fit['range'][0] <= t <= fit['range'][1]:
            excluded['outside_fit_range'].append(t)
        elif t not in by_t:
            excluded['missing_saved_fit_point'].append(t)
        else:
            rows.append({'t': t, 'tvd': _tvd(by_t[t]['values'], ref['values']), 'reference_samples': ref['samples']})
    return {'status': 'available' if rows else 'unavailable', 'count': len(rows),
            'mean_tvd': sum(r['tvd'] for r in rows)/len(rows) if rows else None, 'rows': rows, 'excluded': excluded,
            'reference_draws_per_checkpoint': reference['draws_per_checkpoint'],
            'reference_has_more_draws_at_every_point': reference['draws_per_checkpoint']['min'] > fit['draws_per_checkpoint']['max'],
            'interpretation': 'Saved fit versus measured comparison-run proportions at new checkpoint positions only. This is not comparison against ground truth.'}


def compare_runs(left, right, left_pass_id, right_pass_id):
    """Return an auditable report; incompatible inputs produce reasons, not scores."""
    report = {'schema_version': SCHEMA, 'created_at': datetime.now(timezone.utc).isoformat(),
              'selection': {'left_run_id': left.get('id'), 'left_pass_id': left_pass_id, 'right_run_id': right.get('id'), 'right_pass_id': right_pass_id},
              'compatible': False, 'reasons': [], 'categories': [], 'left': None, 'right': None,
              'raw_shared': None, 'fit_to_observed': None, 'provenance': None, 'caveats': []}
    try:
        a, b = _evidence(left, left_pass_id), _evidence(right, right_pass_id)
        for name in ('prompt_ids', 'gen_ids'):
            if a['record']['base'][name] != b['record']['base'][name]:
                report['reasons'].append(f'Original {name} differ. Token positions cannot be aligned across different traces.')
        lm, rm = left.get('model', {}), right.get('model', {})
        if any(not isinstance(m.get('resolved_revision'), str) or not re.fullmatch(r'(?:[0-9a-fA-F]{40,64}|local-sha256:[a-f0-9]{64})', m['resolved_revision']) for m in (lm, rm)):
            report['reasons'].append('A resolved model revision is missing; pinned model identity cannot be verified.')
        if not same_model_identity(lm, rm):
            report['reasons'].append('Pinned model identity differs or is unverified; cross-model token-position comparison is unsupported.')
        for key in ('dtype', 'architecture', 'vocab_size'):
            if lm.get(key) != rm.get(key): report['reasons'].append(f'Recorded model {key} differs.')
        if left['categories'] != right['categories']: report['reasons'].append('Outcome categories or their order differ.')
        if _parser(left) != _parser(right): report['reasons'].append('Recorded prompt, format, answer tracking or parser differs.')
        if _settings(a) != _settings(b): report['reasons'].append('Sampling settings differ (temperature, candidate top-k, threshold, continuation cap or effective sampling).')
        if left.get('generation_defaults') != right.get('generation_defaults'):
            report['reasons'].append('Recorded model generation defaults differ.')
        for t in sorted(a['candidate_distributions'].keys() & b['candidate_distributions'].keys()):
            lc, rc = a['candidate_distributions'][t], b['candidate_distributions'][t]
            if lc.keys() != rc.keys() or any(abs(lc[token]-rc[token]) > CANDIDATE_TOLERANCE for token in lc):
                report['reasons'].append(f'Retained candidate token IDs/probabilities differ at checkpoint {t} (absolute tolerance {CANDIDATE_TOLERANCE:g}). The sampled mixtures cannot be treated as equivalent.')
        if (left.get('id') == right.get('id') and left_pass_id == right_pass_id) or a['evidence_hash'] == b['evidence_hash']:
            report['reasons'].append('The same observations were selected twice. Comparative metrics are withheld; a zero difference would be tautological, not replication.')
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        report['reasons'].append(str(error))
        return report
    if report['reasons']: return report
    report.update(compatible=True, categories=left['categories'], left=a['summary'], right=b['summary'])
    la, rb = {r['t']: r for r in a['summary']['observed']}, {r['t']: r for r in b['summary']['observed']}
    common = sorted(la.keys() & rb.keys())
    rows = [{'t': t, 'tvd': _tvd(la[t]['values'], rb[t]['values']), 'left_samples': la[t]['samples'], 'right_samples': rb[t]['samples'],
             'retained_mass_difference': abs(la[t]['retained_mass']-rb[t]['retained_mass'])} for t in common]
    report['raw_shared'] = {'status': 'available' if rows else 'unavailable', 'count': len(rows),
                            'mean_tvd': sum(r['tvd'] for r in rows)/len(rows) if rows else None,
                            'max_tvd': max((r['tvd'] for r in rows), default=None), 'rows': rows,
                            'left_only_positions': sorted(la.keys()-rb.keys()), 'right_only_positions': sorted(rb.keys()-la.keys()),
                            'reason': None if rows else 'No shared sampled checkpoint positions. No raw-to-raw score is defined.'}
    report['fit_to_observed'] = {'left_fit_to_right': _direction(a, b), 'right_fit_to_left': _direction(b, a)}
    if left.get('versions') != right.get('versions'):
        for direction in report['fit_to_observed'].values():
            direction.update(status='unavailable', count=0, mean_tvd=None, rows=[],
                             withheld_reason='Recorded library versions differ. Fit-to-reference accuracy interpretation is withheld; the raw difference remains descriptive.')
    same = left.get('id') == right.get('id') and left_pass_id == right_pass_id
    identical = a['evidence_hash'] == b['evidence_hash']
    selection_overlap = len(a['selection_seeds'] & b['selection_seeds'])
    generation_overlap = len(a['generation_seeds'] & b['generation_seeds'])
    rng_status = ('same_observations' if same or identical else 'overlapping_recorded_seeds' if selection_overlap or generation_overlap
                  else 'distinct_recorded_seeds' if a['rng_complete'] and b['rng_complete'] else 'unverified_seed_provenance')
    lineage = {'left': left.get('lineage') or left.get('records', {}).get('replay-verification'),
               'right': right.get('lineage') or right.get('records', {}).get('replay-verification')}
    report['provenance'] = {'same_run': left.get('id') == right.get('id'), 'same_pass': same, 'identical_observation_records': identical,
                            'rng_status': rng_status, 'selection_seed_overlaps': selection_overlap, 'generation_batch_seed_overlaps': generation_overlap,
                            'complete_seed_records': a['rng_complete'] and b['rng_complete'], 'shared_original_trace': True, 'lineage': lineage,
                            'independent_reference_claim': False}
    report['caveats'] = [
        'Raw TVD is half the sum of absolute differences across all recorded outcome proportions. Sampling noise alone can make it positive; it is not a significance test.',
        'The two passes share one fixed trace. Checkpoints are not independent tasks, and choosing a refinement after viewing the source makes this an exploratory comparison.',
        'Saved fits are displayed and evaluated without rerunning reconstruction. Fit scores exclude training checkpoint positions and do not extrapolate outside saved support.',
        'The measured comparison run is a noisy reference. More draws is reported explicitly and does not make it ground truth.',
        'All recorded outcomes, including Other and capped draws, remain in raw proportions. Parser errors and excluded branch mass are not corrected.',
        'Token and collection-time totals cover selected passes, not complete deployment/loading/replay costs. Different checkpoint coverage or draw counts do not demonstrate savings at matched accuracy.',
        'Distinct recorded RNG decisions support separate sampling but do not prove statistical independence or deterministic replay across hardware.'
    ]
    if same or identical: report['caveats'].insert(0, 'This selects the same observations twice. A zero difference is tautological, not a replication.')
    elif rng_status != 'distinct_recorded_seeds': report['caveats'].insert(0, 'Seed provenance overlaps or is incomplete. Do not treat this pair as independent confirmation.')
    if any(row['retained_mass_difference'] > 1e-6 for row in rows):
        report['caveats'].append('Retained branch mass differs at shared checkpoints despite matching settings; numerical replay differences may affect the compared mixtures.')
    if any(x['summary']['cap_fraction'] is not None and x['summary']['cap_fraction'] > .1 for x in (a, b)):
        report['caveats'].append('A selected pass has more than 10% cap hits. Raw scores remain descriptive; truncated outcomes may dominate the difference.')
    if left.get('versions') != right.get('versions'):
        report['caveats'].append('Recorded library versions differ; numerical implementation differences may affect results.')
    return report
