// Pure saved-evidence measurements. No fitting, interpolation, execution or I/O.
const measurementInteger = value => Number.isSafeInteger(value) && value >= 0;
const measurementNumber = value => typeof value === 'number' && Number.isFinite(value);
const measurementPointer = value => typeof value === 'string' && value.startsWith('/') && !/~(?![01])/.test(value);
const measurementOwns = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

export function probabilityVector(values, length) {
  return Array.isArray(values) && values.length === length &&
    values.every(value => measurementNumber(value) && value >= 0 && value <= 1) &&
    Math.abs(values.reduce((sum, value) => sum + value, 0) - 1) <= 1e-6;
}

export function totalVariationDistance(before, after) {
  if (!probabilityVector(before, before?.length) || !probabilityVector(after, before.length)) {
    throw new Error('TVD requires two complete probability distributions over the same outcomes.');
  }
  return before.reduce((sum, value, index) => sum + Math.abs(value - after[index]), 0) / 2;
}

// Ignore arithmetic roundoff at an exact threshold (for example .15 becomes
// .15000000000000002 after subtracting three observed proportions).
export function exceedsThreshold(score, threshold) {
  if (!measurementNumber(score) || !measurementNumber(threshold) || threshold < 0 || threshold > 1) {
    throw new Error('The local-shift score and threshold must be finite; threshold must be between zero and one.');
  }
  return score > threshold + 1e-12;
}

function measurementCategories(categories) {
  if (!Array.isArray(categories) || categories.length < 2 || categories.length > 33 ||
      categories.some(label => typeof label !== 'string' || !label || label.length > 200) ||
      new Set(categories).size !== categories.length) {
    throw new Error('Saved outcome categories must be complete, distinct labels.');
  }
  return [...categories];
}

function measurementHorizonValue(horizon) {
  if (!measurementInteger(horizon) || horizon < 1) {
    throw new Error('The comparison horizon N must be a supported positive integer token distance.');
  }
}

function measurementProvenance(provenance) {
  const params = provenance?.parameters;
  if (!provenance || provenance.fit_status !== 'complete' || provenance.method !== 'M5a_segkernel' ||
      !params || !['linear', 'mult'].includes(params.variant) ||
      !measurementNumber(params.h) || params.h <= 0 || !measurementNumber(params.pen) || params.pen <= 0 ||
      !['cv', 'fixed'].includes(provenance.tuning) ||
      !/^[0-9a-f]{40}$/i.test(provenance.upstream_commit ?? '') ||
      typeof provenance.otrecon_version !== 'string' || !provenance.otrecon_version ||
      !measurementPointer(provenance.pointer)) {
    throw new Error('Smoothed mode requires a complete saved reconstruction with method parameters and recorded revision provenance.');
  }
}

function measurementTokenContext(context, tokenCount) {
  if (context == null) return null;
  if (!Array.isArray(context.items) || !measurementInteger(context.token_start) || !measurementInteger(context.token_end) ||
      context.token_end < context.token_start || !measurementInteger(context.total_tokens) ||
      context.token_end > context.total_tokens ||
      (tokenCount !== null && context.total_tokens !== tokenCount) ||
      context.items.length !== context.token_end - context.token_start ||
      context.items.some((item, index) => item?.t !== context.token_start + index ||
        typeof item.text !== 'string' || !measurementPointer(item.pointer) || typeof item.text_truncated !== 'boolean')) {
    throw new Error('Saved token context has invalid coordinates, text or source pointers.');
  }
  return context;
}

export function buildMeasurementModel(view) {
  if (!view || view.status !== 'ready' || !view.run || !Array.isArray(view.checkpoints)) {
    throw new Error('Measurements require a ready saved sampling view.');
  }
  const categories = measurementCategories(view.run.categories);
  const tokenCount = view.run.token_count ?? null;
  if (tokenCount !== null && (!measurementInteger(tokenCount) || tokenCount < 1)) {
    throw new Error('The saved original token count is invalid.');
  }
  const N = view.run.checkpoint_step ?? null;
  if (N !== null) measurementHorizonValue(N, tokenCount);
  let previous = -1;
  const raw = view.checkpoints.map(point => {
    if (!point || !measurementInteger(point.t) || point.t <= previous ||
        (tokenCount !== null && point.t >= tokenCount) ||
        point.counts_kind !== 'raw_sampled_frequencies' ||
        !measurementInteger(point.n) || point.n < 1 || !point.counts || Array.isArray(point.counts) ||
        Object.keys(point.counts).length !== categories.length ||
        categories.some(label => !measurementOwns(point.counts, label) || !measurementInteger(point.counts[label])) ||
        categories.reduce((sum, label) => sum + point.counts[label], 0) !== point.n) {
      throw new Error('Every checkpoint needs ordered unique token positions and complete nonnegative integer outcome counts summing to its actual sample count.');
    }
    for (const key of ['next_observed_t', 'previous_observed_t']) {
      if (point[key] !== undefined && point[key] !== null && (!measurementInteger(point[key]) ||
          (tokenCount !== null && point[key] >= tokenCount) ||
          (key === 'next_observed_t' ? point[key] <= point.t : point[key] >= point.t))) {
        throw new Error('Saved checkpoint adjacency metadata is invalid.');
      }
    }
    previous = point.t;
    return { ...point, probabilities: categories.map(label => point.counts[label] / point.n) };
  });
  const reconstruction = view.reconstruction ?? { available: false, reason: 'No supported saved reconstruction is available.', points: [] };
  let smooth = [];
  if (reconstruction.available === true) {
    measurementProvenance(reconstruction.provenance);
    if (N === null) throw new Error('Smoothed mode requires a recorded integer comparison horizon.');
    if (!Array.isArray(reconstruction.points) || !reconstruction.points.length || !raw.length) {
      throw new Error('Saved reconstruction points are missing.');
    }
    previous = -1;
    smooth = reconstruction.points.map(point => {
      if (!point || !measurementInteger(point.t) || point.t <= previous ||
          point.t < raw[0].t || point.t > raw.at(-1).t ||
          !probabilityVector(point.probabilities, categories.length) || !measurementPointer(point.pointer)) {
        throw new Error('Saved reconstruction must contain ordered unique supported positions and complete valid probability distributions.');
      }
      if (point.low !== undefined || point.high !== undefined) {
        if (!Array.isArray(point.low) || !Array.isArray(point.high) ||
            point.low.length !== categories.length || point.high.length !== categories.length ||
            point.low.some((value, index) => !measurementNumber(value) || !measurementNumber(point.high[index]) ||
              value < 0 || point.high[index] > 1 || value > point.high[index]) ||
            !measurementPointer(point.bands_pointer?.low) || !measurementPointer(point.bands_pointer?.high)) {
          throw new Error('Saved uncertainty bands are malformed; no bands may be invented.');
        }
      }
      previous = point.t;
      return { ...point, probabilities: [...point.probabilities] };
    });
    if (smooth[0].t !== raw[0].t || smooth.at(-1).t !== raw.at(-1).t ||
        smooth.some((point, index) => index > 0 && point.t !== smooth[index - 1].t + 1)) {
      throw new Error('Smoothed mode requires a dense saved per-token curve across the observed range; gaps are not interpolated.');
    }
  }
  const smoothPositions = new Set(smooth.map(point => point.t));
  const hasSmoothPair = N !== null && smooth.some(point => smoothPositions.has(point.t + N));
  const model = { categories, raw, checkpoints: raw, smooth, N, horizon: N,
    horizons: hasSmoothPair ? [N] : [], tokenCount, tokenContext: measurementTokenContext(view.run.token_context, tokenCount),
    reconstruction, overviewTruncated: view.checkpoints_truncated === true,
    modes: { raw: { available: raw.length > 0,
      reason: view.checkpoints_truncated ? 'The checkpoint overview is capped. Local-shift detection covers only displayed adjacent saved checkpoints.' : null },
      smooth: { available: false, reason: reconstruction.reason ?? 'No exact saved t to t+N endpoints are available.' } } };
  model.modes.smooth.available = hasSmoothPair;
  if (model.modes.smooth.available) model.modes.smooth.reason = null;
  return model;
}

function measurementOptions(model, horizon, mode) {
  if (typeof horizon === 'string') { mode = horizon; horizon = model.N; }
  if (!['raw', 'smooth'].includes(mode)) throw new Error('Unsupported saved measurement mode.');
  if (horizon !== null && horizon !== undefined) {
    measurementHorizonValue(horizon, model.tokenCount);
    if (mode === 'smooth' && !model.horizons.includes(horizon)) throw new Error('The requested N is not a supported saved comparison horizon.');
  }
  if (mode === 'smooth' && (horizon === null || horizon === undefined)) {
    throw new Error('Smoothed comparisons need a recorded supported integer horizon.');
  }
  return { horizon, mode };
}

function measurementInterval(model, before, after, mode) {
  return { mode, t0: before.t, t1: after.t, gap: after.t - before.t,
    before: [...before.probabilities], after: [...after.probabilities],
    score: totalVariationDistance(before.probabilities, after.probabilities),
    counts_before: mode === 'raw' ? before.counts : null,
    counts_after: mode === 'raw' ? after.counts : null,
    n_before: mode === 'raw' ? before.n : null, n_after: mode === 'raw' ? after.n : null,
    pointers: { before: before.pointer ?? null, after: after.pointer ?? null,
      counts_before: mode === 'raw' ? before.counts_source_pointer ?? null : null,
      counts_after: mode === 'raw' ? after.counts_source_pointer ?? null : null,
      n_before: mode === 'raw' ? before.n_pointer ?? null : null,
      n_after: mode === 'raw' ? after.n_pointer ?? null : null },
    provenance: mode === 'smooth' ? model.reconstruction.provenance : {
      kind: 'raw_sampled_frequencies', description: 'Observed label counts divided by the actual sample count at each saved checkpoint.' } };
}

export function supportedIntervals(model, horizon = model.N, mode = 'raw') {
  ({ horizon, mode } = measurementOptions(model, horizon, mode));
  if (mode === 'smooth') {
    const byPosition = new Map(model.smooth.map(point => [point.t, point]));
    return model.smooth.flatMap(point => {
      const after = byPosition.get(point.t + horizon);
      return after ? [measurementInterval(model, point, after, mode)] : [];
    });
  }
  return model.raw.slice(0, -1).flatMap((point, index) => {
    const after = model.raw[index + 1];
    if ((point.next_observed_t !== undefined && point.next_observed_t !== after.t) ||
        (after.previous_observed_t !== undefined && after.previous_observed_t !== point.t) ||
        (model.overviewTruncated && (point.next_observed_t === undefined || after.previous_observed_t === undefined))) return [];
    return [measurementInterval(model, point, after, mode)];
  });
}

export function measurementAt(model, t, horizon = model.N, mode = 'raw') {
  if (!measurementInteger(t)) throw new Error('Selected token position must be a nonnegative integer.');
  const resolved = measurementOptions(model, horizon, mode);
  return supportedIntervals(model, resolved.horizon, resolved.mode).find(pair => resolved.mode === 'raw'
    ? pair.t0 <= t && t < pair.t1 : pair.t0 === t) ?? null;
}

export function localShifts(model, { horizon = model.N, threshold = 0.15, mode = 'raw' } = {}) {
  if (!measurementNumber(threshold) || threshold < 0 || threshold > 1) throw new Error('The local-shift threshold must be between zero and one.');
  return supportedIntervals(model, horizon, mode).filter(pair => exceedsThreshold(pair.score, threshold));
}
