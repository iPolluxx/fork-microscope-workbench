"""Owner isolated, transactional hosted session and worker state machine."""
import hashlib
import hmac
import json
import math
import secrets
import time
import uuid

class HostedError(Exception):
    def __init__(self, status, message): self.status, self.message = status, message

TERMINAL = {'terminated', 'failed'}
JOB_TERMINAL = {'completed','failed','cancelled','interrupted'}

def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':'), allow_nan=False).encode()).hexdigest()
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
def bounded(value, low, high, name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low <= value <= high:
        raise HostedError(422, 'Invalid '+name)
    return value

def safe_capabilities(value):
    value=value if isinstance(value,dict) else {}
    return {'catalog_read':'verified' if value.get('catalog_read')=='verified' else 'unverified',
            'pods_read':'verified' if value.get('pods_read')=='verified' else 'unverified',
            'pods_write':'unverified'}

def safe_account_status(value):
    if not isinstance(value,dict): raise ValueError('Invalid account response')
    readiness=value.get('readiness')
    if readiness not in {'ready','invalid_key','permission_denied','unavailable','credential_unavailable'}: raise ValueError('Invalid account readiness')
    connected=value.get('connected')
    if connected is not None and type(connected) is not bool: raise ValueError('Invalid account connection')
    result={'connected':connected,'readiness':readiness,'capabilities':safe_capabilities(value.get('capabilities')),
            'billing_url':'https://console.runpod.io/billing','write_permissions_verified':False}
    balance=value.get('balance')
    if isinstance(balance,dict):
        status=balance.get('status')
        if status not in {'available','disabled','unsupported','invalid_key','permission_denied','unavailable'}: status='unavailable'
        safe={'status':status,'source':'runpod_graphql_legacy','deprecated':True}
        for name in ('amount_usd','spend_per_hour_usd'):
            number=balance.get(name)
            safe[name]=number if status=='available' and type(number) in (int,float) and math.isfinite(number) else None
        result['balance']=safe
    return result

class HostedService:
    def __init__(self, store, *, clock=time.time, vault=None, catalog=None, lifecycle=None, artifact_prepare=None, artifact_verify=None, credential_validator=None, account_status=None, drive_enabled=True):
        if type(drive_enabled) is not bool: raise ValueError('drive_enabled must be a boolean')
        self.store,self.clock,self.vault,self.catalog,self.lifecycle = store,clock,vault,catalog,lifecycle
        self.artifact_prepare,self.artifact_verify=artifact_prepare,artifact_verify
        self.credential_validator,self.account_status=credential_validator,account_status
        self.drive_enabled=drive_enabled
    def _owned(self, tx, collection, key, uid):
        result=tx.get(collection,key)
        if not result or result.get('owner_uid')!=uid: raise HostedError(404,'Resource not found')
        return result
    def _safe(self, obj):
        return {k:v for k,v in obj.items() if k not in {'worker_hash','enrollment_hash','credential_ref','command','claim_token','download_token','terminal_report_digest'}}
    def public(self, uid, operation, body=None, resource_id=None, idempotency_key=None):
        if not isinstance(uid,str) or not uid or len(uid)>128: raise HostedError(401,'Invalid identity')
        body=body or {}
        try:
            if len(json.dumps(body,allow_nan=False).encode())>65536: raise HostedError(413,'Request too large')
        except (TypeError,ValueError): raise HostedError(422,'Invalid JSON values')
        mutation=operation in {'quote','start','terminate','job','cancel','runpod_put','runpod_delete','received','prepare_plan','approve_plan'}
        if mutation and (not idempotency_key or len(idempotency_key)>128): raise HostedError(400,'Idempotency-Key required (maximum 128 characters)')
        request_digest=digest({'operation':operation,'id':resource_id,'body':body})
        idem=digest([uid,idempotency_key]) if mutation else None
        now=self.clock()
        if idem:
            previous=self.store.transaction(lambda tx: tx.get('idempotency',idem))
            if previous:
                if previous['digest']!=request_digest: raise HostedError(409,'Idempotency key reused with different request')
                return previous['response']
        old_ref=None
        new_ref=None
        credential_claim=None
        if operation in {'runpod_put','runpod_delete'}:
            if self.vault is None: raise HostedError(503,'Credential vault unavailable')
            value=body.get('api_key')
            if operation=='runpod_put' and (not isinstance(value,str) or not 10<=len(value)<=1024): raise HostedError(422,'Invalid API key')
            credential_claim=uuid.uuid4().hex
            def reserve(tx):
                owner=tx.get('owners',uid)
                active=tx.get('sessions',owner['session_id']) if owner else None
                if active and not (active['observed_state']=='terminated' and active.get('cleanup_verified',False)):
                    raise HostedError(409,'Terminate and verify cleanup before changing RunPod credentials')
                conn=tx.get('connections',uid+'_runpod') or {'owner_uid':uid,'connected':False}
                if conn.get('mutation_until',0)>now: raise HostedError(409,'Credential update already in progress')
                conn.update(mutation_claim=credential_claim,mutation_until=now+120)
                tx.set('connections',uid+'_runpod',conn)
                return conn.get('credential_ref')
            old_ref=self.store.transaction(reserve)
            body=dict(body,_credential_claim=credential_claim)
            try:
                if operation=='runpod_put':
                    if self.credential_validator is None: raise HostedError(503,'RunPod credential validation unavailable')
                    try: validation=self.credential_validator(uid,value)
                    except Exception: raise HostedError(503,'RunPod credential validation unavailable') from None
                    if not isinstance(validation,dict) or validation.get('valid') is not True or validation.get('status')!='ready':
                        status=validation.get('status') if isinstance(validation,dict) else None
                        raise HostedError(422 if status in {'invalid_key','permission_denied'} else 503,'RunPod credential validation failed')
                    body['_validation']={'valid':True,'status':'ready','capabilities':safe_capabilities(validation.get('capabilities')),'validated_at':now}
                    new_ref=self.vault.put(uid,'runpod',value)
                    body['_prepared_ref']=new_ref
            except Exception:
                self._release_credential_claim(uid,credential_claim)
                raise
        # Provider quotations are read-only. Secret writes use a deterministic owner/idempotency
        # reference managed by vault; never place credential values in persisted request bodies.
        quote=None
        if operation in {'quote','prepare_plan'}:
            if self.catalog is None: raise HostedError(503,'Catalog unavailable')
            quote_body=body
            if operation=='prepare_plan':
                body=self._prepare_plan_body(body)
                quote_body={'model_id':body['config']['model']['model_id'],'max_usd':body['usd_allowance'],'max_duration_seconds':body['max_duration_seconds']}
            try: quote=self.catalog.quote(dict(quote_body,owner_uid=uid),now)
            except (ValueError,TypeError,KeyError): raise HostedError(422,'Quote unavailable for the requested configuration and allowance') from None
        if operation=='account_status':
            connection=self.store.transaction(lambda tx:tx.get('connections',uid+'_runpod')) or {}
            if not connection.get('connected'): return {'connected':False,'readiness':'disconnected','capabilities':safe_capabilities(None),'write_permissions_verified':False,'billing_url':'https://console.runpod.io/billing'}
            if self.account_status is None: raise HostedError(503,'RunPod account status unavailable')
            try: return safe_account_status(self.account_status(uid))
            except Exception: raise HostedError(503,'RunPod account status unavailable') from None
        def apply(tx):
            if idem:
                previous=tx.get('idempotency',idem)
                if previous:
                    if previous['digest']!=request_digest: raise HostedError(409,'Idempotency key reused with different request')
                    return previous['response']
            applied_at=self.clock()
            result=self._operation(tx,uid,operation,body,resource_id,applied_at,quote)
            if idem: tx.set('idempotency',idem,{'id':idem,'owner_uid':uid,'digest':request_digest,'response':result,'created_at':applied_at})
            return result
        try:
            result=self.store.transaction(apply)
        except Exception:
            if new_ref: self.vault.delete(new_ref)
            if credential_claim: self._release_credential_claim(uid,credential_claim)
            raise
        if old_ref and old_ref!=new_ref: self.vault.delete(old_ref)
        return result
    def _release_credential_claim(self, uid, claim):
        def apply(tx):
            c=tx.get('connections',uid+'_runpod')
            if c and c.get('mutation_claim')==claim:
                c.pop('mutation_claim',None); c.pop('mutation_until',None); tx.set('connections',uid+'_runpod',c)
        self.store.transaction(apply)
    def _prepare_plan_body(self,body):
        fields={'config','usd_allowance','max_duration_seconds','storage_mode','acknowledge_device_loss'}
        if set(body)-fields or not {'config','usd_allowance','max_duration_seconds','storage_mode'}<=set(body): raise HostedError(422,'Invalid investigation plan fields')
        try: config=validate_config(body['config'])
        except (ValueError,TypeError,KeyError): raise HostedError(422,'Invalid investigation config') from None
        bounded(body['usd_allowance'],0.000001,100000,'USD allowance')
        if type(body['max_duration_seconds']) is not int: raise HostedError(422,'Invalid session duration')
        bounded(body['max_duration_seconds'],60,86400,'session duration')
        if body['storage_mode'] not in {'device','drive'}: raise HostedError(422,'Invalid storage mode')
        if body['storage_mode']=='drive' and not self.drive_enabled: raise HostedError(503,'Drive storage is disabled for this deployment')
        if 'acknowledge_device_loss' in body and type(body['acknowledge_device_loss']) is not bool: raise HostedError(422,'Invalid device loss acknowledgment')
        if body['storage_mode']=='device' and body.get('acknowledge_device_loss') is not True: raise HostedError(422,'Device loss acknowledgment required')
        return dict(body,config=config,acknowledge_device_loss=body.get('acknowledge_device_loss',False))
    def _validate_plan_quote(self,b,q,now):
        bounded(q['expires_at'],now+0.01,now+3600,'quote expiry')
        if 'worker_image' in q:
            from .catalog import immutable_worker_image
            try: immutable_worker_image(q['worker_image'])
            except ValueError: raise HostedError(422,'Quote requires an immutable worker image digest') from None
        if q['max_duration_seconds']!=b['max_duration_seconds'] or q.get('max_usd')!=b['usd_allowance']: raise HostedError(422,'Quote differs from requested allowance')
        config=b['config']
        if config['model']['model_id']!=q['model_id'] or config['model']['revision']!=q.get('revision'): raise HostedError(422,'Model identity differs from immutable quote')
        if config['lens'] is not None and config['lens']['profile'] not in q.get('lens_profiles',[]): raise HostedError(422,'Lens profile unavailable for hosted model')
        if config['limits']['max_seconds']+q.get('startup_allowance_seconds',0)+q.get('export_reserve_seconds',60)>q['max_duration_seconds']: raise HostedError(422,'Investigation time leaves insufficient startup or export reserve')
    def _plan_binding(self,p):
        return {k:p[k] for k in ('id','owner_uid','config','config_hash','quote','storage_mode','acknowledge_device_loss','usd_allowance','max_duration_seconds','expires_at','credential_ref')}
    def _operation(self,tx,uid,op,b,key,now,quote,*,internal_plan=None):
        if op=='me': return {'uid':uid,'workspace_id':uid,'runpod_connected':bool((tx.get('connections',uid+'_runpod') or {}).get('connected'))}
        if op=='models': return {'models':self.catalog.models() if self.catalog else []}
        if op=='prepare_plan':
            self._validate_plan_quote(b,quote,now)
            conn=tx.get('connections',uid+'_runpod') or {}
            if not conn.get('connected'): raise HostedError(409,'Connect RunPod first')
            if conn.get('mutation_until',0)>now: raise HostedError(409,'Credential update in progress')
            if b['storage_mode']=='drive':
                drive=tx.get('drive_connections',uid) or {}
                if drive.get('owner')!=uid or drive.get('status')!='connected': raise HostedError(409,'Connect Google Drive first')
            pid=uuid.uuid4().hex
            q=dict(quote,owner_uid=uid,plan_id=pid)
            p=dict(b,id=pid,owner_uid=uid,config_hash=digest(b['config']),quote=q,created_at=now,expires_at=q['expires_at'],status='prepared',credential_ref=conn['credential_ref'])
            p['approval_digest']=digest(self._plan_binding(p))
            tx.set('quotes',q['id'],q);tx.set('plans',pid,p)
            return self._safe(p)
        if op=='plans': return {'plans':[self._safe(p) for p in tx.list('plans') if p['owner_uid']==uid]}
        if op=='plan': return self._safe(self._owned(tx,'plans',key,uid))
        if op=='approve_plan':
            approval=b.get('approval_digest')
            if set(b)!={'approval_digest','confirm_execution'} or b.get('confirm_execution') is not True or not isinstance(approval,str) or len(approval)!=64 or any(c not in '0123456789abcdef' for c in approval): raise HostedError(422,'Exact plan digest and explicit execution approval required')
            p=self._owned(tx,'plans',key,uid)
            if not hmac.compare_digest(p['approval_digest'],b['approval_digest']) or not hmac.compare_digest(p['approval_digest'],digest(self._plan_binding(p))): raise HostedError(409,'Approved plan differs from prepared plan')
            if p.get('session_id'):
                return {'plan':self._safe(p),'session':self._safe(self._owned(tx,'sessions',p['session_id'],uid)),'job':self._safe(self._owned(tx,'jobs',p['job_id'],uid))}
            if p['expires_at']<=now: raise HostedError(409,'Plan expired; prepare and review a new quote')
            self._prepare_plan_body({k:p[k] for k in ('config','usd_allowance','max_duration_seconds','storage_mode','acknowledge_device_loss')})
            self._validate_plan_quote(p,p['quote'],now)
            if digest(p['config'])!=p['config_hash']: raise HostedError(409,'Investigation config changed')
            conn=tx.get('connections',uid+'_runpod') or {}
            if conn.get('credential_ref')!=p['credential_ref']: raise HostedError(409,'RunPod connection changed; prepare a new plan')
            if digest(self._owned(tx,'quotes',p['quote']['id'],uid))!=digest(p['quote']): raise HostedError(409,'Prepared quote changed; prepare a new plan')
            s=self._operation(tx,uid,'start',{'quote_id':p['quote']['id'],'storage_mode':p['storage_mode'],'acknowledge_device_loss':p['acknowledge_device_loss']},None,now,None,internal_plan=p['id'])
            saved=tx.get('sessions',s['id']);saved.update(plan_id=p['id'],config_hash=p['config_hash']);tx.set('sessions',s['id'],saved)
            j=self._operation(tx,uid,'job',{'session_id':s['id'],'command':{'config':p['config']}},None,now,None,internal_plan=p['id'])
            saved['approved_job_id']=j['id'];tx.set('sessions',saved['id'],saved)
            p.update(status='approved',approved_at=now,session_id=s['id'],job_id=j['id']);tx.set('plans',p['id'],p)
            return {'plan':self._safe(p),'session':self._safe(saved),'job':j}
        if op=='quote':
            q=dict(quote,owner_uid=uid,id=quote.get('id') or uuid.uuid4().hex)
            bounded(q['expires_at'],now+0.01,now+3600,'quote expiry')
            bounded(q['max_duration_seconds'],60,86400,'duration')
            tx.set('quotes',q['id'],q); return self._safe(q)
        if op in {'sessions','jobs','artifacts'}: return {op:[self._safe(v) for v in tx.list(op) if v['owner_uid']==uid]}
        if op in {'session','get_job'}: return self._safe(self._owned(tx,'sessions' if op=='session' else 'jobs',key,uid))
        if op=='received':
            a=self._owned(tx,'artifacts',key,uid)
            if a['storage_mode']!='device': raise HostedError(409,'Device receipt required only for device exports')
            a['received_at']=now; tx.set('artifacts',key,a)
            j=self._owned(tx,'jobs',a['job_id'],uid)
            s=self._owned(tx,'sessions',a['session_id'],uid)
            if j['status'] in JOB_TERMINAL:
                remaining=[item for item in tx.list('artifacts') if item['job_id']==j['id'] and item['storage_mode']=='device' and not item.get('received_at')]
                if not remaining: s['desired_state']='terminated'; tx.set('sessions',s['id'],s)
            return {'received':True,'desired_state':s['desired_state']}
        if op=='download':
            a=self._owned(tx,'artifacts',key,uid)
            s=self._owned(tx,'sessions',a['session_id'],uid)
            if a['storage_mode']!='device':
                if not self.drive_enabled: raise HostedError(503,'Drive storage is disabled for this deployment')
                return {'file_ref':a['file_ref'],'sha256':a['sha256']}
            if s['expires_at']<=now or s['observed_state'] in TERMINAL: raise HostedError(410,'Artifact download expired')
            pod=s.get('provider_ref') or s.get('pod_id')
            if not isinstance(pod,str) or not pod.isalnum(): raise HostedError(503,'Download unavailable')
            return {'url':'https://'+pod+'-8780.proxy.runpod.net/bundles/'+a['id'], 'download_token':a['download_token'],'sha256':a['sha256'],'size':a['size']}
        if op=='drive':
            if not self.drive_enabled: return {'enabled':False,'connected':False}
            connection=tx.get('drive_connections',uid) or {}
            return {'enabled':True,'connected':connection.get('owner')==uid and connection.get('status')=='connected'}
        if op in {'runpod_put','runpod_delete'}:
            if self.vault is None: raise HostedError(503,'Credential vault unavailable')
            connection=tx.get('connections',uid+'_runpod') or {}
            if connection.get('mutation_claim')!=b.get('_credential_claim'): raise HostedError(409,'Credential update fenced')
            # Validation and vault I/O happen outside the transaction. The lease
            # may expire while they run, allowing a session to pin the old key.
            # Recheck admission before replacing or deleting that cleanup key.
            if connection.get('mutation_until',0)<=now: raise HostedError(409,'Credential update expired; retry validation')
            owner=tx.get('owners',uid)
            active=tx.get('sessions',owner['session_id']) if owner else None
            if active and not (active['observed_state']=='terminated' and active.get('cleanup_verified',False)):
                raise HostedError(409,'Terminate and verify cleanup before changing RunPod credentials')
            if op=='runpod_put':
                value=b.get('api_key')
                if not isinstance(value,str) or not 10<=len(value)<=1024: raise HostedError(422,'Invalid API key')
                ref=b['_prepared_ref']
                tx.set('connections',uid+'_runpod',{'owner_uid':uid,'credential_ref':ref,'connected':True,'validation':b['_validation']})
            else: tx.set('connections',uid+'_runpod',{'owner_uid':uid,'connected':False})
            return {'connected':op=='runpod_put',**({'validation':b['_validation']} if op=='runpod_put' else {})}
        if op=='start':
            q=self._owned(tx,'quotes',b.get('quote_id',''),uid)
            if q.get('plan_id') and internal_plan!=q['plan_id']: raise HostedError(409,'Approve the exact investigation plan to use this quote')
            if q['expires_at']<=now: raise HostedError(409,'Quote expired')
            mode=b.get('storage_mode')
            if mode not in {'device','drive'}: raise HostedError(422,'Invalid storage mode')
            if mode=='drive':
                if not self.drive_enabled: raise HostedError(503,'Drive storage is disabled for this deployment')
                drive=tx.get('drive_connections',uid) or {}
                if drive.get('owner')!=uid or drive.get('status')!='connected': raise HostedError(409,'Connect Google Drive first')
            if mode=='device' and b.get('acknowledge_device_loss') is not True: raise HostedError(422,'Device loss acknowledgment required')
            active=tx.get('owners',uid)
            if active:
                old=tx.get('sessions',active['session_id'])
                if old and not (old['observed_state']=='terminated' and old.get('cleanup_verified',False)): raise HostedError(409,'An active session or unverified cleanup already exists')
            sid=uuid.uuid4().hex
            s={'id':sid,'owner_uid':uid,'desired_state':'running','observed_state':'requested','created_at':now,'expires_at':now+q['max_duration_seconds'],'quote':q,'request_name':'fm-'+sid,'storage_mode':mode,'epoch':0,'seq':-1,'lease_until':0}
            conn=tx.get('connections',uid+'_runpod')
            if conn and conn.get('mutation_until',0)>now: raise HostedError(409,'Credential update in progress')
            if not conn or not conn.get('connected'): raise HostedError(409,'Connect RunPod first')
            s['credential_ref']=conn['credential_ref']
            tx.set('sessions',sid,s); tx.set('owners',uid,{'owner_uid':uid,'session_id':sid}); return self._safe(s)
        if op=='terminate':
            s=self._owned(tx,'sessions',key,uid); s['desired_state']='terminated'; tx.set('sessions',key,s); return self._safe(s)
        if op=='job':
            s=self._owned(tx,'sessions',b.get('session_id',''),uid)
            if s['storage_mode']=='drive' and not self.drive_enabled: raise HostedError(503,'Drive storage is disabled for this deployment')
            if s.get('plan_id') and (internal_plan!=s['plan_id'] or s.get('approved_job_id')): raise HostedError(409,'Plan session accepts only its approved investigation')
            if s['desired_state']!='running' or s['observed_state'] in TERMINAL or s['expires_at']<=now: raise HostedError(409,'Session unavailable')
            if any(j['session_id']==s['id'] and j['status'] not in JOB_TERMINAL for j in tx.list('jobs')): raise HostedError(409,'Session has an unfinished job')
            command=b.get('command')
            if not isinstance(command,dict): raise HostedError(422,'Command object required')
            # Exact model comes from accepted immutable quote, never substituted by worker.
            if command.get('model_id',s['quote'].get('model_id'))!=s['quote'].get('model_id'): raise HostedError(422,'Model differs from quote')
            
            try: config=validate_config(command.get('config'))
            except (ValueError,TypeError,KeyError): raise HostedError(422,'Invalid investigation config')
            if config['lens'] is not None and config['lens']['profile'] not in s['quote'].get('lens_profiles',[]): raise HostedError(422,'Lens profile unavailable for hosted model')
            if config['model']['model_id']!=s['quote']['model_id'] or config['model']['revision']!=s['quote'].get('revision'): raise HostedError(422,'Model identity differs from immutable quote')
            reserve=s['quote'].get('export_reserve_seconds',60)
            if config['limits']['max_seconds']>s['expires_at']-now-reserve: raise HostedError(422,'Job time exceeds remaining session allowance')
            j={'id':uuid.uuid4().hex,'owner_uid':uid,'session_id':s['id'],'status':'queued','command':command,'created_at':now}
            tx.set('jobs',j['id'],j); return self._safe(j)
        if op=='cancel':
            j=self._owned(tx,'jobs',key,uid)
            if j['status'] not in JOB_TERMINAL:
                j['cancel_requested']=True
                if j['status']=='queued':
                    j['status']='cancelled'; j.pop('command',None)
                    s=self._owned(tx,'sessions',j['session_id'],uid)
                    if s.get('plan_id'): s['desired_state']='terminated';tx.set('sessions',s['id'],s)
                tx.set('jobs',key,j)
            return self._safe(j)
        raise HostedError(404,'Unknown operation')
    def prepare_enrollment(self, session_id):
        token=secrets.token_urlsafe(32)
        def apply(tx):
            s=tx.get('sessions',session_id)
            if not s or s['desired_state']!='running' or s['observed_state'] in TERMINAL or s['expires_at']<=self.clock(): raise HostedError(409,'Session unavailable')
            # A restart uses its private journal's existing worker lease. Issuing
            # a new epoch while a job is active would strand the old job forever.
            if s.get('worker_hash'): raise HostedError(409,'Worker already enrolled')
            if s['storage_mode']=='drive' and not self.drive_enabled: raise HostedError(503,'Drive storage is disabled for this deployment')
            s['enrollment_hash']=token_hash(token); s['enrollment_expires_at']=min(self.clock()+600,s['expires_at']); tx.set('sessions',session_id,s)
        self.store.transaction(apply); return token
    def enroll(self,b):
        bearer=secrets.token_urlsafe(32)
        def apply(tx):
            s=tx.get('sessions',b.get('session_id',''))
            if not s or not s.get('enrollment_hash') or not hmac.compare_digest(s['enrollment_hash'],token_hash(str(b.get('token','')))) or s['enrollment_expires_at']<=self.clock() or s['desired_state']!='running': raise HostedError(401,'Invalid enrollment')
            if s['storage_mode']=='drive' and not self.drive_enabled: raise HostedError(503,'Drive storage is disabled for this deployment')
            s.pop('enrollment_hash'); s['worker_hash']=token_hash(bearer); s['epoch']+=1; s['seq']=-1; s['lease_until']=min(self.clock()+90,s['expires_at']); s['worker_last_heartbeat_at']=self.clock(); s['observed_state']='ready'; s['idle_since']=self.clock(); tx.set('sessions',s['id'],s)
            return {'worker_token':bearer,'epoch':s['epoch'],'lease_until':s['lease_until']}
        return self.store.transaction(apply)
    def _worker(self,tx,b):
        s=tx.get('sessions',b.get('session_id',''))
        if not s or s['observed_state'] in TERMINAL or not hmac.compare_digest(s.get('worker_hash',''),token_hash(str(b.get('worker_token','')))) or b.get('epoch')!=s['epoch'] or s['lease_until']<=self.clock(): raise HostedError(401,'Worker lease expired or fenced')
        return s
    def poll(self,b):
        def apply(tx):
            s=self._worker(tx,b)
            if s['desired_state']=='terminated' or s['expires_at']<=self.clock(): return {'action':'terminate'}
            jobs=[j for j in tx.list('jobs') if j['session_id']==s['id'] and j['status'] not in JOB_TERMINAL]
            if s['storage_mode']=='drive' and not self.drive_enabled:
                for j in jobs:
                    if j['status']=='queued':
                        j.update(status='cancelled',finished_at=self.clock());j.pop('command',None);tx.set('jobs',j['id'],j)
                jobs=[j for j in jobs if j['status'] not in JOB_TERMINAL]
                if not jobs:
                    s['desired_state']='terminated';tx.set('sessions',s['id'],s)
                    return {'action':'terminate'}
            if not jobs: return {'action':'idle'}
            j=jobs[0]
            if j.get('cancel_requested'): return {'action':'cancel','job_id':j['id']}
            if j['status']!='queued': return {'action':'wait','job_id':j['id']}
            # A delayed startup must not consume the reviewed export reserve or
            # silently shrink the approved investigation's execution allowance.
            config=(j.get('command') or {}).get('config') or {}
            if config.get('limits',{}).get('max_seconds',float('inf'))>s['expires_at']-self.clock()-s['quote'].get('export_reserve_seconds',60):
                j.update(status='cancelled',finished_at=self.clock(),error_code='insufficient_execution_time');j.pop('command',None);tx.set('jobs',j['id'],j)
                s['desired_state']='terminated';tx.set('sessions',s['id'],s)
                return {'action':'terminate'}
            j.update(status='running',epoch=s['epoch'],started_at=self.clock(),dispatch_ack_deadline=self.clock()+60); tx.set('jobs',j['id'],j)
            s['observed_state']='running'; s['has_started_job']=True; tx.set('sessions',s['id'],s)
            return {'action':'run','job_id':j['id'],'command':j['command'],'model':s['quote'].get('model_id'),'epoch':s['epoch'],'expires_at':s['expires_at'],'destination':s['storage_mode']}
        return self.store.transaction(apply)
    def heartbeat(self,b):
        def apply(tx):
            s=self._worker(tx,b)
            seq=b.get('seq')
            if isinstance(seq,bool) or not isinstance(seq,int) or seq<=s['seq']: raise HostedError(409,'Nonmonotonic worker sequence')
            active=b.get('active_job_id')
            if active:
                j=tx.get('jobs',active)
                # A heartbeat racing the terminal report must not rewrite the
                # saved job's progress after its exact result was recorded.
                if j and j['session_id']==s['id'] and j.get('epoch')==s['epoch'] and j['status'] not in JOB_TERMINAL:
                    j['dispatch_acknowledged']=True; j.pop('command',None)
                    progress=b.get('progress')
                    if progress is not None:
                        if not isinstance(progress,dict) or set(progress) not in ({'phase','completed','total'},{'phase','completed','total','progress_unit'}) or progress['phase'] not in {'loading','generating','scanning','refining','inspecting','saving'}:
                            raise HostedError(422,'Invalid progress')
                        for field in ('completed','total'):
                            if type(progress[field]) is not int or not 0<=progress[field]<=10**9: raise HostedError(422,'Invalid progress count')
                        if 'progress_unit' in progress and progress['progress_unit'] not in {'samples','continuations','checkpoints','tokens','steps','layers','bytes'}: raise HostedError(422,'Invalid progress unit')
                        j['progress']=progress
                    tx.set('jobs',j['id'],j)
            s['worker_last_heartbeat_at']=self.clock(); s['seq']=seq; s['lease_until']=min(self.clock()+90,s['expires_at']); tx.set('sessions',s['id'],s)
            return {'lease_until':s['lease_until'],'desired_state':s['desired_state']}
        return self.store.transaction(apply)
    def report(self,b):
        # Remote verification must never run inside a retried DB transaction (or
        # while the in-memory transaction lock is held by split-app tests).
        def inspect(tx):
            s=self._worker(tx,b);j=self._owned(tx,'jobs',b.get('job_id',''),s['owner_uid'])
            if j['session_id']!=s['id'] or j.get('epoch')!=s['epoch']: raise HostedError(409,'Job fenced')
            return s,j
        snapshot,job=self.store.transaction(inspect)
        state=b.get('status')
        if state not in {'saving','completed','failed','cancelled','interrupted'}: raise HostedError(422,'Invalid report state')
        artifacts=b.get('artifacts',[])
        if not isinstance(artifacts,list) or len(artifacts)>32 or (state=='completed' and not artifacts): raise HostedError(422,'Completed job requires valid artifacts')
        if state=='saving' and artifacts: raise HostedError(422,'Saving report must not contain artifacts')
        for artifact in artifacts:
            if not isinstance(artifact,dict): raise HostedError(422,'Invalid artifact metadata')
            size=artifact.get('size',artifact.get('size_bytes'))
            if type(size) is not int or not 1<=size<=67108864: raise HostedError(422,'Invalid artifact size')
            checksum=artifact.get('sha256')
            if not isinstance(checksum,str) or len(checksum)!=64 or any(c not in '0123456789abcdef' for c in checksum): raise HostedError(422,'Invalid artifact checksum')
            identifier=artifact.get('artifact_id') or artifact.get('id')
            if not isinstance(identifier,str) or not identifier.isascii() or not identifier.isalnum() or not 1<=len(identifier)<=128: raise HostedError(422,'Invalid artifact identifier')
            ref=artifact.get('file_ref') or identifier
            if not isinstance(ref,str) or not 1<=len(ref)<=2048: raise HostedError(422,'Invalid artifact reference')
            if snapshot['storage_mode']=='device':
                capability=artifact.get('download_token')
                if not isinstance(capability,str) or not capability.isascii() or not 32<=len(capability)<=128: raise HostedError(422,'Invalid download capability')
        report_digest=digest({'status':state,'artifacts':artifacts})
        # Verification may normalize opaque Drive references. Keep that private
        # copy separate from the request whose exact retry digest is persisted.
        b=dict(b,artifacts=json.loads(json.dumps(artifacts)))
        if snapshot['storage_mode']=='drive' and job['status'] not in JOB_TERMINAL and state in JOB_TERMINAL and (state=='completed' or b.get('artifacts')):
            if not self.artifact_verify: raise HostedError(503,'Drive verification unavailable')
            self.artifact_verify(snapshot,job,b.get('artifacts',[]))
        def apply(tx):
            s=self._worker(tx,b); j=self._owned(tx,'jobs',b.get('job_id',''),s['owner_uid'])
            if j['session_id']!=s['id'] or j.get('epoch')!=s['epoch']: raise HostedError(409,'Job fenced')
            state=b.get('status')
            if state not in {'saving','completed','failed','cancelled','interrupted'}: raise HostedError(422,'Invalid report state')
            if j['status'] in JOB_TERMINAL:
                if j['status']==state:
                    if j.get('terminal_report_digest') and j['terminal_report_digest']!=report_digest: raise HostedError(409,'Terminal report differs from saved result')
                    return self._safe(j)
                raise HostedError(409,'Job already terminal')
            if state in JOB_TERMINAL and (state=='completed' or b.get('artifacts')):
                artifacts=b.get('artifacts',[])
                if not artifacts or len(artifacts)>32: raise HostedError(422,'Completed job requires artifacts')
                for a in artifacts:
                    bounded(a.get('size',a.get('size_bytes')),1,67108864,'artifact size')
                    checksum=a.get('sha256',''); ref=a.get('file_ref') or a.get('id') or a.get('artifact_id','')
                    if len(checksum)!=64 or any(c not in '0123456789abcdef' for c in checksum) or not isinstance(ref,str) or not 1<=len(ref)<=2048: raise HostedError(422,'Invalid artifact metadata')
                    item={'id':a.get('artifact_id') or a.get('id') or uuid.uuid4().hex,'owner_uid':s['owner_uid'],'job_id':j['id'],'session_id':s['id'],'sha256':checksum,'file_ref':ref,'storage_mode':s['storage_mode']}
                    item['size']=bounded(a.get('size',a.get('size_bytes')),1,67108864,'artifact size')
                    if s['storage_mode']=='device':
                        capability=a.get('download_token','')
                        if not isinstance(capability,str) or not 32<=len(capability)<=128 or not item['id'].isalnum() or len(item['id'])>128: raise HostedError(422,'Invalid download capability')
                        item['download_token']=capability
                    if tx.get('artifacts',item['id']): raise HostedError(409,'Artifact already exists')
                    tx.set('artifacts',item['id'],item)
            j['status']=state
            # Only worker-owned states advance here; a controller-owned
            # 'terminating' session must not appear ready again.
            worker_owned=s['observed_state'] in {'ready','running','saving'}
            if state in JOB_TERMINAL:
                j.pop('command',None); j['finished_at']=self.clock(); j['terminal_report_digest']=report_digest
                if worker_owned: s['observed_state']='ready'
                if s['storage_mode']=='drive' or not b.get('artifacts'): s['desired_state']='terminated'
            elif worker_owned: s['observed_state']='saving'
            tx.set('jobs',j['id'],j); tx.set('sessions',s['id'],s); return self._safe(j)
        return self.store.transaction(apply)
    def prepare_artifact(self,b):
        def read(tx):
            s=self._worker(tx,b); j=self._owned(tx,"jobs",b.get("job_id",""),s["owner_uid"])
            if j["session_id"]!=s["id"] or j.get("epoch")!=s["epoch"] or j["status"] in JOB_TERMINAL: raise HostedError(409,"Job fenced")
            return s,j
        s,j=self.store.transaction(read)
        if not self.artifact_prepare: raise HostedError(503,"Artifact upload unavailable")
        return self.artifact_prepare(s,j,b)
    def persist_before_create(self, session_id, patch):
        def apply(tx):
            s=tx.get("sessions",session_id)
            if not s or s.get("create_attempted") or s["desired_state"]!="running": return False
            if s['storage_mode']=='drive' and not self.drive_enabled:
                s['desired_state']='terminated';tx.set('sessions',session_id,s);return False
            s.update(patch); tx.set("sessions",session_id,s); return True
        return self.store.transaction(apply)
    def reconcile(self):
        now=self.clock()
        def claim(tx):
            claimed=[]
            for s in tx.list('sessions'):
                if s['observed_state']=='terminated' and s.get('cleanup_verified',True): continue
                if s['expires_at']<=now: s['desired_state']='terminated'
                if s['storage_mode']=='drive' and not self.drive_enabled and not any(
                        j['session_id']==s['id'] and j['status'] in {'running','saving'} for j in tx.list('jobs')):
                    s['desired_state']='terminated'
                for j in tx.list('jobs'):
                    if j['session_id']==s['id'] and j['status']=='running' and not j.get('dispatch_acknowledged') and j.get('dispatch_ack_deadline',float('inf'))<=now:
                        j.update(status='interrupted',finished_at=now); j.pop('command',None); tx.set('jobs',j['id'],j); s['desired_state']='terminated'
                if s.get('worker_hash') and s['lease_until']<=now:
                    for j in tx.list('jobs'):
                        if j['session_id']==s['id'] and j['status'] in {'running','saving'}:
                            j.update(status='interrupted',finished_at=now); j.pop('command',None); tx.set('jobs',j['id'],j)
                    s.pop('worker_hash',None); s['desired_state']='terminated'
                if s['desired_state']=='terminated':
                    for j in tx.list('jobs'):
                        if j['session_id']==s['id'] and j['status']=='queued':
                            j.update(status='cancelled',finished_at=now);j.pop('command',None);tx.set('jobs',j['id'],j)
                if s.get('claim_until',0)>now:
                    # Persist cancellation even while another reconciliation owns
                    # the provider lease; its create claim must see the new intent.
                    tx.set('sessions',s['id'],s)
                    continue
                s['claim_token']=uuid.uuid4().hex; s['claim_until']=now+120
                tx.set('sessions',s['id'],s); claimed.append(s)
            return claimed
        sessions=self.store.transaction(claim)
        for s in sessions:
            if not self.lifecycle: continue
            try: patch=self.lifecycle.reconcile_session(s)
            except Exception: patch={'controller_error':'Provider reconciliation failed'}
            def finish(tx):
                current=tx.get('sessions',s['id'])
                if current.get('claim_token')!=s['claim_token']: return
                # Desired state is independently owned by API mutations.
                if patch.get('desired_state')=='terminated': current['desired_state']='terminated'
                changes={k:v for k,v in patch.items() if k not in {'id','owner_uid','desired_state','worker_hash','enrollment_hash'}}
                # Enrollment, dispatch and terminal reporting may finish while
                # a provider read is in flight. These states belong to the worker;
                # a controller echo must neither regress nor resurrect them.
                if current['observed_state'] in {'ready','running','saving'} and changes.get('observed_state') in {'requested','provisioning','booting','ready','running','saving'}:
                    changes.pop('observed_state',None)
                if 'controller_error' not in patch:
                    current.pop('controller_error',None)
                if not patch.get('error_code') and current.get('error_code') in {'create_outcome_pending','provider_unavailable','credential_unavailable'}:
                    current.pop('error_code',None)
                current.update(changes)
                if current['desired_state']=='terminated':
                    # Provider-driven deadlines/rejections can set termination
                    # only after claim(). Close queued jobs now, not on a later
                    # tick that verified cleanup may correctly skip altogether.
                    cleaned=current['observed_state']=='terminated' and current.get('cleanup_verified') is True
                    for job in tx.list('jobs'):
                        if job['session_id']==current['id'] and job['status'] not in JOB_TERMINAL and (job['status']=='queued' or cleaned):
                            job.update(status='cancelled' if job['status']=='queued' else 'interrupted',finished_at=self.clock(),
                                       error_code=current.get('error_code') or 'session_terminated')
                            job.pop('command',None);tx.set('jobs',job['id'],job)
                current['claim_until']=0; tx.set('sessions',s['id'],current)
            self.store.transaction(finish)
        return {'reconciled':len(sessions)}


def validate_config(c):
    if not isinstance(c,dict) or set(c)!={'model','base','scan','refinement','lens','limits'}:raise ValueError('Config requires model, base, scan, refinement, lens and limits.')
    if any(not isinstance(c[k],dict) for k in ('model','base','scan','refinement','limits')):raise ValueError('Model, base, scan, refinement and limits must be objects.')
    if c['lens'] is not None and not isinstance(c['lens'],dict):raise ValueError('Lens must be an object or null.')
    m=c['model'];b=c['base'];s=c['scan'];r=c['refinement'];limits=c['limits']
    if set(m)!={'model_id','revision','device','batch_size'} or not all(isinstance(m[k],str) and m[k] for k in ('model_id','revision')):raise ValueError('Specify model identity and pinned revision.')
    if m['revision'] in ('main','master','latest'):raise ValueError('Use a pinned model revision, not a moving branch.')
    if m['device'] not in ('auto','cpu','cuda'):raise ValueError('Invalid model device.')
    def whole(x,lo,hi,name):
        if type(x) is not int or not lo<=x<=hi:raise ValueError(f'{name} must be an integer between {lo} and {hi}.')
    whole(m['batch_size'],1,128,'Batch size')
    if set(b)!={'prompt','answers','mode','max_tokens','seed'} or not isinstance(b['prompt'],str) or not 1<=len(b['prompt'].strip())<=16000:raise ValueError('Invalid prompt settings.')
    answers=b['answers']
    if not isinstance(answers,list) or not 2<=len(answers)<=50 or any(not isinstance(a,str) or not 1<=len(a)<=256 for a in answers) or len(set(answers))!=len(answers): raise ValueError('Invalid answers.')
    if b['mode'] not in ('chat','base'):raise ValueError('Invalid prompt mode.')
    whole(b['max_tokens'],8,4096,'Base token cap');whole(b['seed'],0,2**31-1,'Seed')
    if set(s)!={'start','end','stride','samples','cont_max','temperature','top_k','threshold','seed'}:raise ValueError('Invalid scan settings.')
    whole(s['start'],0,4095,'Scan start')
    if s['end'] is not None:whole(s['end'],s['start']+1,4095,'Scan end')
    for key,lo,hi in [('stride',1,128),('samples',5,512),('cont_max',1,4096),('top_k',1,50),('seed',0,2**31-1)]:whole(s[key],lo,hi,key)
    for key,lo,hi in [('temperature',.05,2),('threshold',0,1)]:
        if type(s[key]) not in (int,float) or not math.isfinite(s[key]) or not lo<=s[key]<=hi:raise ValueError('Invalid '+key)
    if set(r)!={'max_rounds','stride','samples','min_tvd'}:raise ValueError('Invalid refinement policy.')
    whole(r['max_rounds'],0,8,'Refinement rounds');whole(r['stride'],1,128,'Refinement stride');whole(r['samples'],5,512,'Refinement samples')
    if type(r['min_tvd']) not in (int,float) or not math.isfinite(r['min_tvd']) or not 0<=r['min_tvd']<=1:raise ValueError('Invalid TVD threshold.')
    if set(limits)!={'max_seconds','max_samples','max_generated_tokens'}:raise ValueError('Supply time, sample and generated-token limits.')
    for k in limits:whole(limits[k],1,10**9,k)
    l=c['lens']
    if l is not None:
        fields={'profile','layers','before','after','top_k'}
        if set(l) not in (fields, fields | {'inspection_backend'}) or not isinstance(l['profile'],str):raise ValueError('Invalid lens settings.')
        if l.get('inspection_backend','native') not in ('native','nnsight'):raise ValueError('Lens inspection_backend must be native or nnsight.')
        if not isinstance(l['layers'],list) or not 1<=len(l['layers'])<=16 or any(type(x) is not int for x in l['layers']) or len(set(l['layers']))!=len(l['layers']):raise ValueError('Select unique lens layers.')
        for layer in l['layers']:whole(layer,0,999,'Lens layer')
        for k,lo,hi in [('before',0,16),('after',0,16),('top_k',2,30)]:whole(l[k],lo,hi,k)
    digest(c)
    return json.loads(json.dumps(c))
