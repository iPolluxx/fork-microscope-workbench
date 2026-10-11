import test from 'node:test';
import assert from 'node:assert/strict';
import { buildMeasurementModel, supportedIntervals, measurementAt, localShifts,
  probabilityVector, totalVariationDistance, exceedsThreshold } from '../ui/measurements.mjs';

const point = (t, counts, n = counts.reduce((a, b) => a + b, 0)) => ({
  t, n, counts: Object.fromEntries(['A', 'B', 'Other'].map((label, i) => [label, counts[i]])),
  counts_kind: 'raw_sampled_frequencies', pointer: `/positions/${t}`,
  counts_source_pointer: '/branches', n_pointer: `/positions/${t}/samples`,
});
const fixture = () => ({ status: 'ready', run: { categories: ['A', 'B', 'Other'],
  token_count: 85, checkpoint_step: 16 }, checkpoints: [
    point(0, [16, 2, 2]), point(16, [13, 6, 1]), point(32, [17, 1, 2]),
    point(48, [18, 2, 0]), point(64, [17, 0, 3]), point(80, [15, 0, 5])],
  reconstruction: { available: false, reason: 'Not saved', points: [] } });

function withFit(view = fixture()) {
  view.reconstruction = { available: true, points: Array.from({ length: view.checkpoints.at(-1).t - view.checkpoints[0].t + 1 },
    (_, i) => view.checkpoints[0].t + i).map(t => ({
    t, probabilities: [0.8 - t / 400, 0.1 + t / 400, 0.1], pointer: `/curve/smoothed/${t}`,
  })), provenance: { method: 'M5a_segkernel', method_recorded: false,
    method_basis: 'Application method', fit_status: 'complete', parameters: { h: 8, pen: 1, variant: 'linear' },
    tuning: 'cv', upstream_commit: 'd32fed8d4162a4888291c4b3a38b059727c85a41',
    otrecon_version: '1.0.0', pointer: '/curve' } };
  return view;
}

test('all raw measurements share actual interval, denominator and source pointers', () => {
  const model = buildMeasurementModel(fixture());
  const pairs = supportedIntervals(model, 16, 'raw');
  assert.deepEqual(pairs.map(p => [p.t0, p.t1, p.gap]), [[0, 16, 16], [16, 32, 16], [32, 48, 16], [48, 64, 16], [64, 80, 16]]);
  const pair = measurementAt(model, 16, 'raw');
  assert.deepEqual(pair, pairs[1]);
  assert.deepEqual(pair.before, [.65, .3, .05]);
  assert.deepEqual(pair.after, [.85, .05, .1]);
  assert.deepEqual([pair.n_before, pair.n_after], [20, 20]);
  assert.ok(Math.abs(pair.score - .25) < 1e-12);
  assert.equal(pair.pointers.before, '/positions/16');
  assert.equal(pair.pointers.counts_before, '/branches');
  assert.equal(pair.pointers.n_after, '/positions/32/samples');
  assert.deepEqual(localShifts(model, { threshold: .2 }).map(p => [p.t0, p.t1]), [[16, 32]]);
  assert.deepEqual(measurementAt(model, 23, 'raw'), pairs[1]);
  assert.equal(measurementAt(model, 80, 'raw'), null);
  assert.equal(measurementAt(model, 100, 'raw'), null);
});

test('count validation rejects total10/n20, incomplete categories, negatives, fractions and booleans', () => {
  for (const edit of [p => { p.n = 20; p.counts = { A: 8, B: 1, Other: 1 }; },
    p => { delete p.counts.Other; }, p => { p.counts.Other = -1; },
    p => { p.counts.A = 15.5; }, p => { p.counts.B = true; }, p => { p.n = 20.5; },
    p => { p.counts.extra = 0; }, p => { p.counts_kind = 'weighted_estimates'; },
    p => { p.n = 0; }, p => { p.n = Number.MAX_SAFE_INTEGER + 1; }]) {
    const view = fixture(); edit(view.checkpoints[0]);
    assert.throws(() => buildMeasurementModel(view), /checkpoint/);
  }
});

test('category and token coordinate validation refuses duplicates and unsorted or fractional positions', () => {
  for (const edit of [v => { v.run.categories = ['A', 'A', 'Other']; },
    v => { v.checkpoints[1].t = 0; }, v => { v.checkpoints[1].t = 1.5; },
    v => { v.checkpoints[1].t = -1; }, v => { v.checkpoints[1].t = 100; },
    v => { v.checkpoints.reverse(); }]) {
    const view = fixture(); edit(view); assert.throws(() => buildMeasurementModel(view));
  }
});

test('variable denominators and irregular spacing use actual adjacent observed positions', () => {
  const view = fixture(); view.run.checkpoint_step = 4;
  view.checkpoints = [point(3, [8, 1, 1], 10), point(11, [13, 6, 1], 20), point(29, [1, 3, 1], 5)];
  const model = buildMeasurementModel(view), pairs = supportedIntervals(model, 4, 'raw');
  assert.deepEqual(pairs.map(p => [p.t0, p.t1, p.gap, p.n_before, p.n_after]), [[3, 11, 8, 10, 20], [11, 29, 18, 20, 5]]);
  assert.deepEqual(pairs[0].before, [.8, .1, .1]);
  assert.deepEqual(pairs[1].after, [.2, .6, .2]);
  assert.equal(measurementAt(model, 0, 'raw'), null);
  assert.deepEqual(measurementAt(model, 12, 'raw'), pairs[1]);
  assert.equal(measurementAt(model, 29, 'raw'), null);
});

test('N1.5 and other invalid horizons cannot pass validation then crash indexing', () => {
  for (const N of [1.5, 0, -1, true, '16', NaN, Infinity]) {
    const view = fixture(); view.run.checkpoint_step = N;
    assert.throws(() => buildMeasurementModel(view), /horizon/);
    const model = buildMeasurementModel(fixture());
    assert.throws(() => supportedIntervals(model, N, 'smooth'));
  }
  const model = buildMeasurementModel(withFit());
  assert.throws(() => measurementAt(model, 0, 8, 'smooth'), /not a supported/);
  assert.throws(() => measurementAt(model, 1.5, 'raw'), /integer/);
});

test('large legitimate stride does not hide raw evidence from short traces', () => {
  const view = fixture(); view.run.checkpoint_step = 100;
  const model = buildMeasurementModel(view);
  assert.equal(model.modes.raw.available, true);
  assert.equal(model.modes.smooth.available, false);
  assert.equal(supportedIntervals(model, 100, 'raw').length, 5);
});

test('raw-only sources never fabricate smoothing or dense token probabilities', () => {
  const model = buildMeasurementModel(fixture());
  assert.deepEqual(model.smooth, []);
  assert.equal(model.modes.smooth.available, false);
  assert.equal(model.modes.smooth.reason, 'Not saved');
  assert.equal(localShifts(model, { threshold: 1 }).length, 0);
});

test('saved dense reconstruction with nonzero origin compares exact coordinates rather than array indices', () => {
  const view = fixture(); view.run.checkpoint_step = 4;
  view.checkpoints = [point(3, [8, 1, 1]), point(11, [13, 6, 1]), point(29, [1, 3, 1])];
  const model = buildMeasurementModel(withFit(view));
  assert.equal(model.modes.smooth.available, true);
  assert.equal(supportedIntervals(model, 4, 'smooth').length, 23);
  const pair = measurementAt(model, 3, 4, 'smooth');
  assert.deepEqual([pair.t0, pair.t1, pair.gap], [3, 7, 4]);
  assert.equal(pair.n_before, null);
  assert.equal(pair.counts_after, null);
  assert.equal(pair.pointers.before, '/curve/smoothed/3');
  assert.equal(pair.provenance.parameters.h, 8);
  assert.equal(pair.provenance.method_recorded, false);
  assert.equal(measurementAt(model, 0, 'smooth'), null);
  assert.equal(measurementAt(model, 29, 'smooth'), null);
  const sparse = withFit(); sparse.reconstruction.points = sparse.reconstruction.points.filter(p => p.t % 16 === 0);
  assert.throws(() => buildMeasurementModel(sparse), /dense saved per-token/);
});

test('probabilities need complete categories,0..1 finite range and sum1', () => {
  for (const vector of [[1.2, -.3, .1], [.5, .4], [.5, .4, .05], [.5, .4, NaN], [.5, .4, true], [.5, .4, Infinity]]) {
    assert.equal(probabilityVector(vector, 3), false);
    const view = withFit(); view.reconstruction.points[0].probabilities = vector;
    assert.throws(() => buildMeasurementModel(view), /probability distributions/);
    assert.throws(() => totalVariationDistance(vector, [.8, .1, .1]));
  }
  assert.equal(totalVariationDistance([1, 0, 0], [0, 1, 0]), 1);
  assert.equal(totalVariationDistance([.8, .1, .1], [.8, .1, .1]), 0);
});

test('saved method provenance and valid uncertainty bounds are required and retained', () => {
  for (const edit of [v => { delete v.reconstruction.provenance; },
    v => { v.reconstruction.provenance.parameters.h = -1; },
    v => { v.reconstruction.provenance.upstream_commit = 'unknown'; },
    v => { v.reconstruction.provenance.fit_status = 'withheld'; },
    v => { v.reconstruction.points[0].t = 1.5; },
    v => { v.reconstruction.points[1].t = 0; },
    v => { v.reconstruction.points[0].low = [0, 0, 0]; }]) {
    const view = withFit(); edit(view); assert.throws(() => buildMeasurementModel(view));
  }
  const view = withFit();
  Object.assign(view.reconstruction.points[0], { low: [.5, .01, .01], high: [.99, .5, .5],
    bands_pointer: { low: '/curve/low/0', high: '/curve/high/0' } });
  assert.deepEqual(buildMeasurementModel(view).smooth[0].low, [.5, .01, .01]);
});

test('capped overview never connects omitted checkpoints as observed adjacency', () => {
  const view = fixture(); view.checkpoints_truncated = true;
  view.checkpoints = [view.checkpoints[0], view.checkpoints.at(-1)];
  view.checkpoints[0].next_observed_t = 16; view.checkpoints[0].previous_observed_t = null;
  view.checkpoints[1].next_observed_t = null; view.checkpoints[1].previous_observed_t = 64;
  const model = buildMeasurementModel(view);
  assert.deepEqual(supportedIntervals(model, 'raw'), []);
  assert.match(model.modes.raw.reason, /capped/);
  const legacy = fixture(); legacy.checkpoints_truncated = true;
  assert.deepEqual(supportedIntervals(buildMeasurementModel(legacy), 'raw'), []);
});

test('saved token text and exact pointers retain Unicode/untrusted strings without execution', () => {
  const view = fixture();
  view.run.token_context = { items: [{ t: 2, text: '🙂</script>', pointer: '/base/tokens/2', text_truncated: false }],
    token_start: 2, token_end: 3, total_tokens: 85, truncated: true, concatenates_to_response: false };
  assert.equal(buildMeasurementModel(view).tokenContext.items[0].text, '🙂</script>');
  view.run.token_context.items[0].t = 3;
  assert.throws(() => buildMeasurementModel(view), /token context/);
});

test('empty thresholds and mode errors do not imply globally steady or noise classification', () => {
  const model = buildMeasurementModel(fixture());
  assert.deepEqual(localShifts(model, { threshold: 1 }), []);
  for (const threshold of [-.1, 1.1, true, NaN]) assert.throws(() => localShifts(model, { threshold }));
  assert.throws(() => supportedIntervals(model, 16, 'causal'));
  const single = fixture(); single.checkpoints = single.checkpoints.slice(0, 1);
  assert.deepEqual(supportedIntervals(buildMeasurementModel(single), 'raw'), []);
});

test('exact15percentTVD never crosses a15percent threshold from floating-point roundoff', () => {
  const model = buildMeasurementModel(fixture());
  const pair = measurementAt(model, 48, 'raw');
  assert.ok(Math.abs(pair.score - .15) < 1e-12);
  assert.equal(exceedsThreshold(pair.score, .15), false);
  assert.deepEqual(localShifts(model, { threshold: .15 }).map(p => p.t0), [0, 16]);
  assert.equal(exceedsThreshold(.15001, .15), true);
  assert.equal(exceedsThreshold(.15, .15), false);
});
