"""Model-native HF adapter. Reuses upstream token-space decoding and sampling."""
import inspect
import json
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import torch
from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, AutoProcessor, AutoTokenizer
from huggingface_hub import constants as hub_constants
from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.errors import EntryNotFoundError
from huggingface_hub.utils import tqdm as HubProgress
from transformers.utils.import_utils import is_env_variable_true
from forking_paths.model import ForkingModel
from forking_paths.prompts import format_mmlu_base, format_mmlu_instruct_user
from fork_microscope.model_preflight import model_location, local_model_identity, local_model_files, context_limit, validate_model_request, native_loader


def cache_weights(model_id, revision, activity=None, check=lambda: None):
    """Cache only the native safetensors set, reporting real Hub progress."""
    check()
    try:
        index = hf_hub_download(model_id, 'model.safetensors.index.json', revision=revision)
    except EntryNotFoundError:
        names = ['model.safetensors']
    else:
        names = sorted(set(json.loads(Path(index).read_text())['weight_map'].values()))
        if not names or any(not isinstance(n,str) or PurePosixPath(n).is_absolute() or
                '..' in PurePosixPath(n).parts or not n.endswith('.safetensors') for n in names):
            raise ValueError('The safetensors index contains invalid shard paths.')

    class DownloadProgress(HubProgress):
        def display(self, *args, **kwargs):
            pass  # The dashboard is the progress surface.

        def update(self, n=1):
            value = super().update(n)
            check()
            # Disabled Hub/Xet bars omit tqdm's display/counter attributes.
            # Still honor cancellation, but do not manufacture progress.
            if self.disable:
                return value
            now = time.monotonic()
            if activity and now - getattr(self, '_last_report', 0) >= .4:
                self._last_report = now
                name = getattr(self, 'name', '') or ''
                if self.unit == 'B' and 'transfer' not in name and 'Downloading bytes' not in self.desc:
                    elapsed = time.time()-self.start_t
                    rate = max(0,self.n-self.initial)/elapsed if elapsed>=2 else None
                    activity(dict(kind='download', completed=self.n, total=self.total or None, unit='bytes',rate=rate))
                elif self.unit != 'B':
                    activity(dict(kind='download', completed=self.n, total=self.total or None, unit='files'))
            return value

    snapshot_download(model_id, revision=revision, allow_patterns=names, tqdm_class=DownloadProgress)
    check()


class AttachedModel(ForkingModel):
    def __init__(self, model_id, revision="main", device="auto", gen_batch=4, progress=None, activity=None, check=None):
        started = time.perf_counter()
        self.activity, self.check_job = activity, check or (lambda: None)
        report = progress or (lambda phase: None)
        validate_model_request(dict(model_id=model_id, revision=revision, device=device, batch_size=gen_batch))
        source_type, model_id = model_location(model_id)
        local_identity = None
        local_stats = None
        if source_type == 'local':
            local_identity = local_model_identity(model_id, progress=report)
            if revision.startswith('local-sha256:') and revision != local_identity:
                raise ValueError('Local model contents differ from the saved source identity. Restore the exact model snapshot before replaying.')
            local_stats = {str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino)
                           for p in local_model_files(model_id)}
        report("Reading model configuration…")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable in this runtime. Choose CPU or run the dashboard on a GPU machine.")
        self.device, self.gen_batch, self.seed = device, gen_batch, 0
        self.enable_prefix_caching = False  # Ordinary generate caching; no cross-branch reuse claim.
        torch.set_num_threads(min(4, torch.get_num_threads()))
        config = AutoConfig.from_pretrained(model_id, revision=None if source_type == 'local' else revision, trust_remote_code=False)
        config_done = time.perf_counter()
        if context_limit(config) is None:
            raise ValueError('The model configuration does not declare a supported context limit.')
        if getattr(config, 'quantization_config', None):
            raise ValueError('Pre-quantized models are not supported by this full-weight adapter. Select native safetensors weights.')
        # Resolve mutable Hub refs once so tokenizer and weights use the same snapshot.
        load_revision = getattr(config, "_commit_hash", None) if source_type == 'hub' else None
        if source_type == 'hub' and not load_revision:
            raise ValueError('Could not pin the model to an immutable Hub revision. Inspect the model again before loading.')
        self.is_muse = config.model_type == "muse_glimmer"
        # generated: share loader selection with metadata preflight. Keep the
        # native Qwen 3.5/3.6 wrapper so layer paths and checkpoint weights align.
        loader_name = native_loader(config)
        if loader_name is None:
            raise ValueError('This architecture has no supported native text-generation loader in the installed Transformers version.')
        report("Loading tokenizer and chat template…")
        if self.is_muse:
            processor = AutoProcessor.from_pretrained(model_id, revision=load_revision, trust_remote_code=False)
            self.tokenizer = processor.tokenizer
        else:
            self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=load_revision, trust_remote_code=False)
        tokenizer_done = time.perf_counter()
        if source_type == 'hub':
            report('Downloading missing model weights (cached files are reused)…')
            cache_weights(model_id, load_revision, activity, self.check_job)
        self.check_job()
        report("Loading cached weights into " + device + " memory…")
        loader = AutoModelForImageTextToText if loader_name == 'AutoModelForImageTextToText' else AutoModelForCausalLM
        dtype = torch.float32 if device == "cpu" else (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
        self.model = loader.from_pretrained(model_id, revision=load_revision, config=config,
            dtype=dtype, trust_remote_code=False, use_safetensors=True,
            device_map={"": device}).eval()
        if local_stats is not None:
            current = {str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino)
                       for p in local_model_files(model_id)}
            if current != local_stats:
                raise ValueError('Local model files changed while loading. Retry with an unchanged snapshot.')
        if device == "cuda":
            torch.cuda.synchronize()
        weights_done = time.perf_counter()
        report("Validating tokenizer and completion markers…")
        text_config = getattr(self.model.config, "text_config", self.model.config)
        ids = self.model.generation_config.eos_token_id
        self.eos_ids = list(ids) if isinstance(ids, list) else ([] if ids is None else [ids])
        if self.tokenizer.eos_token_id is not None:
            self.eos_ids = sorted(set(self.eos_ids + [self.tokenizer.eos_token_id]))
        if self.is_muse:
            vocab = self.tokenizer.get_vocab()
            if "<|eot|>" not in vocab:
                raise ValueError("Muse tokenizer lacks its expected end-of-turn marker.")
            self.eos_ids = sorted(set(self.eos_ids + [vocab[x] for x in ("<|eot|>","<|end_of_text|>") if x in vocab]) - {vocab.get("<|eom|>")})
        if self.tokenizer.pad_token_id is None:
            if self.tokenizer.eos_token_id is None:
                raise ValueError("This tokenizer needs an EOS or padding token.")
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        self.info = dict(model_id=model_id, requested_revision=revision,
            resolved_revision=local_identity or load_revision, device=device,
            source_type=source_type, identity_kind='local_content_sha256' if local_identity else 'hub_commit',
            dtype=str(dtype), parameters=sum(p.numel() for p in self.model.parameters()),
            context_limit=context_limit(self.model.config),
            vocab_size=text_config.vocab_size, architecture=type(self.model).__name__,
            # generated: record the concrete loader for inspection provenance.
            loader=loader_name, input_modalities=['text'],
            chat_template=bool(self.tokenizer.chat_template), batch_size=gen_batch,
            capabilities=dict(exact_tokens=True, next_token_logits=True, forced_prefix=True,
                chat=bool(self.tokenizer.chat_template)),
            loading=dict(config_seconds=config_done-started,
                tokenizer_seconds=tokenizer_done-config_done,
                weights_seconds=weights_done-tokenizer_done,
                total_seconds=time.perf_counter()-started,
                async_weight_loading_requested=not is_env_variable_true("HF_DEACTIVATE_ASYNC_LOAD"),
                xet_high_performance=hub_constants.HF_XET_HIGH_PERFORMANCE,
                hub_cache=hub_constants.HF_HUB_CACHE))

    def prompt(self, question, choices, mode):
        if mode == "chat":
            if not self.tokenizer.chat_template:
                raise ValueError("This tokenizer has no chat template. Choose base/completion mode.")
            return list(self.tokenizer.apply_chat_template(
                [{"role": "user", "content": format_mmlu_instruct_user(question, choices)}],
                tokenize=True, add_generation_prompt=True, return_dict=False))
        return list(self.tokenizer(format_mmlu_base(question, choices), add_special_tokens=True)["input_ids"])

    def prompt_text(self, text, mode):
        if mode == 'chat':
            if not self.tokenizer.chat_template:
                raise ValueError('This tokenizer has no chat template. Choose base/completion mode.')
            return list(self.tokenizer.apply_chat_template(
                [{'role': 'user', 'content': text}], tokenize=True,
                add_generation_prompt=True, return_dict=False))
        return list(self.tokenizer(text, add_special_tokens=True)['input_ids'])

    @torch.no_grad()
    def logit_read_letters(self, prefixes, seed=0):
        self.letter_ids = [self.tokenizer(x, add_special_tokens=False)['input_ids'] for x in 'ABCD']
        if any(len(x) != 1 for x in self.letter_ids) or len({x[0] for x in self.letter_ids}) != 4:
            raise ValueError('Legacy logit readout requires distinct single-token A–D labels.')
        # Process one prefix at a time to bound answer-extraction memory.
        out = []
        supports_last = "logits_to_keep" in inspect.signature(self.model.forward).parameters or self.is_muse
        for prefix in prefixes:
            ids = torch.tensor([prefix], device=self.device)
            kw = {"logits_to_keep": 1} if supports_last else {}
            logits = self.model(ids, attention_mask=torch.ones_like(ids), **kw).logits[0, -1]
            out.append("ABCD"[int(logits[[x[0] for x in self.letter_ids]].argmax())])
        return out

    @contextmanager
    def decoding_progress(self, cap, check, batch=1, batches=1):
        steps = 0
        last_report = 0.
        callback = getattr(self, 'activity', None)
        def tick(module, args, output):
            nonlocal steps, last_report
            check()
            steps += 1
            now = time.monotonic()
            if callback and (steps == 1 or now-last_report >= .4):
                callback(dict(kind='generation', step=steps, cap=cap, batch=batch, batches=batches))
                last_report = now
        hook = self.model.register_forward_hook(tick)
        try:
            yield
        finally:
            hook.remove()

    def base_path(self, prompt_ids, max_tokens, **kwargs):
        with self.decoding_progress(max_tokens, getattr(self, 'check_job', lambda: None)):
            return super().base_path(prompt_ids, max_tokens=max_tokens, **kwargs)

    def draw_branch(self, branch, count, cap, temperature, seed, check):
        if branch.tok_id in self.eos_ids:
            return [[] for _ in range(count)]
        result = []
        for lo in range(0, count, self.gen_batch):
            check()
            n = min(self.gen_batch, count-lo)
            with self.decoding_progress(cap, check, lo//self.gen_batch+1, (count+self.gen_batch-1)//self.gen_batch):
                sampled, _ = self.resample([branch.prefix_ids], n=n, max_tokens=cap,
                    temperature=temperature, seed=(seed+lo) % (2**31-1))
            result.extend(sampled[0])
        return result
