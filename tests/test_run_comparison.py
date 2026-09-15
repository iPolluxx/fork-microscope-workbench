import copy
import json
import math

from fork_microscope.run_comparison import compare_runs


def fixture(run_id='a', positions=(0, 2, 4), counts=None, seed=7, samples=4):
    categories = ['yes', 'no', 'Other']
    counts = counts or {t: [2, 2, 0] for t in positions}
    config = {'temperature': 1., 'top_k': 2, 'threshold': .05, 'cont_max': 16}
    rec = {'sampling_design': 'position_mixture_v1', 'categories': categories,
           'meta': {'matching': 'answer_text_anywhere_v1'},
           'base': {'prompt_ids': [1, 2], 'gen_ids': list(range(10, 20)), 'finish_reason': 'stop'},
           'config': {'cont_temperature': 1., 'top_k': 2, 'p_thresh': .05, 'cont_max_tokens': 16}, 'positions': [], 'branches': []}
    values = []
    for t in positions:
        labels = [c for c, n in zip(categories, counts[t]) for _ in range(n)]
        n = len(labels)
        values.append([c/n for c in counts[t]])
        rec['positions'].append({'t': t, 'samples': n, 'retained_mass': .98, 'selection_seed': [seed, 0, t, 101],
                                 'candidates': [{'tok_id': 10+t, 'tok_p': .98}]})
        rec['branches'].append({'t': t, 'tok_id': 10+t, 'seed': seed*100+t,
                                'draw_indices': list(range(n)), 'answers': labels, 'cont_lens': [2]*n,
                                'observations': [{'label': label, 'stop_reason': 'eos'} for label in labels]})
    curve = {'parameters': {'h': 1}, 'fit_status': 'complete', 'positions': list(positions), 'weighted': values,
             'support': list(range(min(positions), max(positions)+1)),
             'smoothed': [[.5, .5, 0] for _ in range(min(positions), max(positions)+1)]}
    return {'id': run_id*32, 'schema_version': 2, 'sampling_design': 'position_mixture_v1', 'categories': categories,
            'model': {'model_id': 'fixture-model', 'resolved_revision': 'f'*40, 'dtype': 'float32', 'batch_size': 1},
            'settings': config, 'effective_sampling': {'branch_temperature': 1., 'continuation_temperature': 1., 'top_p': 1., 'top_k': 0},
            'generation_defaults': {'repetition_penalty': 1}, 'versions': {'fixture': '1'},
            'base': {'question': {'question': 'A fixture question', 'answers': ['yes', 'no'], 'matching': 'answer_text_anywhere_v1'}},
            'base_config': {'prompt': 'A fixture question', 'answers': ['yes', 'no'], 'mode': 'chat'},
            'passes': [{'id': 'pass_1', 'label': 'Example', 'curve': curve}], 'records': {'pass_1': rec},
            'measured': {'pass_1': {'wall_seconds': 4.5}}}


def test_known_zero_and_nonzero_raw_tvd():
    a, b = fixture(), fixture('b', seed=19)
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['compatible'] and r['raw_shared']['mean_tvd'] == 0
    b = fixture('b', counts={t: [4, 0, 0] for t in [0, 2, 4]}, seed=19)
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['raw_shared']['mean_tvd'] == .5 and r['raw_shared']['count'] == 3
    assert r['left']['generated_tokens'] == 24 and r['left']['draws'] == 12
    json.dumps(r, allow_nan=False)


def test_incompatible_settings_trace_parser_revision_no_scores():
    a = fixture()
    for mutate in [
        lambda b: b['settings'].update(temperature=.7),
        lambda b: b['records']['pass_1']['base']['gen_ids'].append(99),
        lambda b: b['model'].update(resolved_revision='main'),
        lambda b: b['model'].update(model_id='another-model'),
        lambda b: b['base_config'].update(prompt='Another question'),
        lambda b: b['base']['question'].update(matching='last_match_v2'),
        lambda b: b['categories'].reverse(),
    ]:
        b = fixture('b'); mutate(b)
        r = compare_runs(a, b, 'pass_1', 'pass_1')
        assert not r['compatible'] and r['reasons'] and r['raw_shared'] is None and r['left'] is None


def test_count_spacing_can_differ_and_fit_uses_only_measured_new_positions():
    a = fixture(positions=(0, 2, 4))
    b = fixture('b', positions=(1, 2, 5), counts={t: [8, 0, 0] for t in (1, 2, 5)}, seed=19)
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['compatible'] and r['raw_shared']['count'] == 1
    direction = r['fit_to_observed']['left_fit_to_right']
    assert direction['rows'] == [{'t': 1, 'tvd': .5, 'reference_samples': 8}]
    assert direction['excluded']['training_checkpoint_overlap'] == [2]
    assert direction['excluded']['outside_fit_range'] == [5]
    assert direction['reference_has_more_draws_at_every_point']


def test_verified_local_contents_can_move_between_worker_paths():
    a, b = fixture(), fixture('b', seed=19)
    for run, path in ((a, '/worker-a/models/model'), (b, '/worker-b/mounted/model')):
        run['model'].update(model_id=path, source_type='local', identity_kind='local_content_sha256',
                            resolved_revision='local-sha256:' + 'a'*64)
    assert compare_runs(a, b, 'pass_1', 'pass_1')['compatible']
    b['model']['resolved_revision'] = 'local-sha256:' + 'b'*64
    assert not compare_runs(a, b, 'pass_1', 'pass_1')['compatible']
    for run in (a, b):
        run['model']['resolved_revision'] = 'local-sha256:not-a-fingerprint'
    assert not compare_runs(a, b, 'pass_1', 'pass_1')['compatible']


def test_no_shared_or_supported_positions_is_not_zero_error():
    a, b = fixture(positions=(0, 1)), fixture('b', positions=(8, 9), seed=19)
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['compatible'] and r['raw_shared']['status'] == 'unavailable'
    assert r['raw_shared']['mean_tvd'] is None
    assert r['fit_to_observed']['left_fit_to_right']['mean_tvd'] is None


def test_same_source_lineage_does_not_imply_independence():
    a, b = fixture(), fixture('b')
    b['lineage'] = {'source_run_id': a['id'], 'exact_ids_verified': True}
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert not r['compatible'] and r['raw_shared'] is None and 'same observations' in ' '.join(r['reasons'])
    b['records']['pass_1']['branches'][0]['observations'][0]['note'] = 'a copied record with a changed note'
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['provenance']['rng_status'] == 'overlapping_recorded_seeds'
    assert not r['provenance']['independent_reference_claim']
    b = fixture('b', seed=19); b['lineage'] = {'source_run_id': a['id']}
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['provenance']['rng_status'] == 'distinct_recorded_seeds'
    del b['records']['pass_1']['positions'][0]['selection_seed']
    assert compare_runs(a, b, 'pass_1', 'pass_1')['provenance']['rng_status'] == 'unverified_seed_provenance'
    assert not compare_runs(a, a, 'pass_1', 'pass_1')['compatible']


def test_candidate_distribution_identity_and_missing_metadata():
    a = fixture()
    b = fixture('b', seed=19)
    b['records']['pass_1']['positions'][0]['candidates'][0]['tok_p'] -= 5e-6
    assert compare_runs(a, b, 'pass_1', 'pass_1')['compatible']
    b = fixture('b', seed=19)
    b['records']['pass_1']['positions'][0]['candidates'] = [{'tok_id': 10, 'tok_p': .9}, {'tok_id': 99, 'tok_p': .08}]
    assert not compare_runs(a, b, 'pass_1', 'pass_1')['compatible']
    b = fixture('b', seed=19)
    del b['records']['pass_1']['positions'][0]['candidates']
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert not r['compatible'] and 'missing' in ' '.join(r['reasons'])


def test_library_changes_withhold_fit_accuracy_but_keep_raw_description():
    a, b = fixture(), fixture('b', positions=(0, 1, 2, 4), seed=19)
    b['versions']['fixture'] = '2'
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['compatible'] and r['raw_shared']['count'] == 3
    assert r['fit_to_observed']['left_fit_to_right']['mean_tvd'] is None
    assert 'versions differ' in r['fit_to_observed']['left_fit_to_right']['withheld_reason']


def test_bad_draws_or_nonfinite_values_do_not_produce_scores():
    a, b = fixture(), fixture('b')
    b['records']['pass_1']['branches'][0]['draw_indices'][0] = 1
    assert not compare_runs(a, b, 'pass_1', 'pass_1')['compatible']
    b = fixture('b'); b['records']['pass_1']['positions'][0]['retained_mass'] = math.nan
    assert not compare_runs(a, b, 'pass_1', 'pass_1')['compatible']


def test_cap_other_and_corrupt_fit_are_explicit():
    a, b = fixture(counts={t: [1, 1, 2] for t in (0, 2, 4)}), fixture('b', seed=19)
    for branch in a['records']['pass_1']['branches']:
        branch['observations'][0]['stop_reason'] = 'length'
    a['passes'][0]['curve']['weighted'][0] = [1., 0., 0.]
    r = compare_runs(a, b, 'pass_1', 'pass_1')
    assert r['left']['cap_fraction'] == .25 and r['left']['other_fraction'] == .5
    assert r['left']['fit'] == [] and 'do not agree' in r['left']['fit_note']
    assert any('10%' in note for note in r['caveats'])


def test_dense_reference_is_selectable_without_fit():
    a, b = fixture(), fixture('b', positions=(0, 1, 2, 3, 4), seed=19)
    b['records']['dense'] = b['records'].pop('pass_1')
    b['reference'] = {'positions': [0, 1, 2, 3, 4]}
    b['measured']['dense'] = b['measured'].pop('pass_1')
    r = compare_runs(a, b, 'pass_1', 'dense')
    assert r['compatible'] and r['right']['fit'] == []
    assert [row['t'] for row in r['fit_to_observed']['left_fit_to_right']['rows']] == [1, 3]


def test_input_not_modified():
    a, b = fixture(), fixture('b', seed=19)
    before = copy.deepcopy([a, b]); compare_runs(a, b, 'pass_1', 'pass_1')
    assert [a, b] == before
