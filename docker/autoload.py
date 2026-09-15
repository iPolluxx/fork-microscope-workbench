"""Load the selected model after the local dashboard starts; never run an experiment."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8767'


def enabled(env=None):
    env = os.environ if env is None else env
    value = env.get('AUTO_LOAD_MODEL', env.get('AUTO_LOAD_MUSE', '0'))
    if value not in ('0', '1'):
        raise ValueError('AUTO_LOAD_MODEL (or legacy AUTO_LOAD_MUSE) must be 0 or 1.')
    return value == '1'


def load_config(env=None, root=ROOT):
    """Profile defaults plus explicit deployment overrides, without changing the profile."""
    env = os.environ if env is None else env
    model_id = env.get('FORK_MODEL_ID')
    revision = env.get('FORK_MODEL_REVISION')
    profile = env.get('FORK_MODEL_PROFILE')
    if profile:
        path = Path(profile)
        if not path.is_absolute():
            path = root / path
        config = json.loads(path.read_text())
        payload = dict(config.get('load', config))
        if set(payload) != {'model_id', 'revision', 'device', 'batch_size'}:
            raise ValueError('Model profile requires model_id, revision, device and batch_size only in its load object.')
        if model_id and model_id != payload['model_id'] and not revision:
            raise ValueError('Changing a profile model requires FORK_MODEL_REVISION; another model cannot inherit its revision.')
    elif model_id:
        payload = dict(model_id=model_id, revision=revision or 'main', device='auto', batch_size=1)
    else:
        if any(env.get(k) for k in ('FORK_MODEL_REVISION', 'FORK_MODEL_DEVICE', 'FORK_MODEL_BATCH_SIZE')):
            raise ValueError('Set FORK_MODEL_ID or FORK_MODEL_PROFILE before overriding model settings.')
        return None
    for key, value in [('model_id', model_id), ('revision', revision),
                       ('device', env.get('FORK_MODEL_DEVICE'))]:
        if value:
            payload[key] = value
    if env.get('FORK_MODEL_BATCH_SIZE'):
        try:
            payload['batch_size'] = int(env['FORK_MODEL_BATCH_SIZE'])
        except ValueError as exc:
            raise ValueError('FORK_MODEL_BATCH_SIZE must be an integer.') from exc
    for key in ('model_id', 'revision'):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError(f'{key} must be a nonempty model identifier/path or revision (up to 500 characters).')
    if payload['device'] not in ('auto', 'cpu', 'cuda'):
        raise ValueError('Model device must be auto, cpu or cuda.')
    if type(payload['batch_size']) is not int or not 1 <= payload['batch_size'] <= 128:
        raise ValueError('Model batch_size must be an integer from 1 to 128.')
    return payload


def request_json(path, payload=None):
    headers = {'Content-Type': 'application/json', 'Origin': URL}
    if os.environ.get('FORK_WORKER_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['FORK_WORKER_TOKEN']
    request = urllib.request.Request(URL + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--print-config', action='store_true', help='Validate and print selected model; no network calls.')
    args = parser.parse_args()
    if not args.print_config and not enabled():
        print('Automatic model loading disabled. Choose a model in the dashboard.', flush=True)
        return
    config = load_config()
    if args.print_config:
        print(json.dumps({'auto_load': enabled(), 'load': config}, indent=2))
        return
    if config is None:
        raise ValueError('Automatic loading requires an explicit FORK_MODEL_ID or FORK_MODEL_PROFILE.')
    for attempt in range(120):
        try:
            request_json('/api/live/status')
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    else:
        raise RuntimeError('Dashboard did not start; selected model was not loaded.')
    result = request_json('/api/live/load', config)
    job_id = result['job_id']
    print('Automatic model load requested:', json.dumps(config), flush=True)
    previous = None
    while True:
        status = request_json('/api/live/status')
        job = status['job']
        if job['id'] != job_id:
            raise RuntimeError('Automatic load job was replaced; inspect dashboard status.')
        if job['phase'] != previous:
            print(job['phase'], flush=True)
            previous = job['phase']
        if job['status'] != 'running':
            if job['status'] != 'complete' or not status.get('model'):
                raise RuntimeError('Automatic model load did not complete: ' + job['phase'])
            print('Model ready:', json.dumps(status['model']), flush=True)
            return
        time.sleep(1)


if __name__ == '__main__':
    main()
