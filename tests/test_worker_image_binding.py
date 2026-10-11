"""Worker image review, migration and cleanup with fake providers only."""
import copy

import pytest

from fork_microscope.hosted.catalog import Catalog, MODELS
from fork_microscope.hosted.lifecycle import Lifecycle
from fork_microscope.hosted.provider import FakeRunPod
from fork_microscope.hosted.service import HostedError
from test_hosted_plans import setup, prepare, approve

IMAGE_A='example/worker@sha256:'+'a'*64
IMAGE_B='example/worker@sha256:'+'b'*64


def lifecycle(session,provider,*,image=IMAGE_A,legacy=False,touches=None):
    touches=touches if touches is not None else []
    def factory(_):touches.append('provider');return provider
    def claim(_,patch):touches.append('claim');session.update(patch);return True
    def environment(_):touches.append('enrollment');return {'FM_ENROLLMENT_TOKEN':'FAKE-CAPABILITY'}
    return Lifecycle(factory,worker_image=image,control_plane_url='https://fake.example',
        persist_before_create=claim,worker_environment=environment,clock=lambda:1001.,allow_unbound_quotes=legacy)


def session(image=IMAGE_A):
    quote=dict(model_id=MODELS[0]['id'],model_revision=MODELS[0]['revision'],worker_image=image,
        gpu_id='fake-gpu',disk_gb=30,expires_at=1100.,startup_allowance_seconds=300)
    return dict(id='session1',owner_uid='fake-owner',request_name='fm-session1',created_at=1000.,expires_at=1600.,
        desired_state='running',observed_state='requested',quote=quote,storage_mode='device')


@pytest.mark.parametrize('value',['latest','example/worker:latest','example/worker@sha256:'+'g'*64,
    'example/worker@sha256:'+'a'*63,'example/worker @sha256:'+'a'*64,123])
def test_catalog_rejects_malformed_image_before_provider_reads(value):
    def forbidden(_):raise AssertionError('Invalid image must not touch the provider')
    with pytest.raises(ValueError,match='Immutable worker image'):
        Catalog(forbidden,disk_usd_per_gb_hour=0,disk_rate_source='fake',worker_image=value)


def test_image_quote_is_bound_in_exact_approval_and_config_change_prevents_create(setup):
    service,provider,_,_=setup
    plan=prepare(service)
    assert plan['quote']['worker_image']==IMAGE_A
    accepted=approve(service,plan)
    stored=service.store.transaction(lambda tx:tx.get('sessions',accepted['session']['id']))
    touches=[]
    replacement=lifecycle(stored,provider,image=IMAGE_B,touches=touches)
    decision=replacement.reconcile_session(stored)
    assert decision['desired_state']=='terminated' and decision['error_code']=='worker_image_review_required'
    assert touches==[] and provider.creates==0
    # Operator restart reconstructs the controller; it does not update the stored quote.
    again=lifecycle(copy.deepcopy(stored),provider,image=IMAGE_B,touches=touches).reconcile_session(stored)
    assert again==decision and touches==[]


def test_tampering_with_plan_image_invalidates_approval_without_creating_session(setup):
    service,provider,_,_=setup;plan=prepare(service)
    def change(tx):
        stored=tx.get('plans',plan['id']);stored['quote']['worker_image']=IMAGE_B;tx.set('plans',plan['id'],stored)
    service.store.transaction(change)
    with pytest.raises(HostedError) as error:approve(service,plan)
    assert error.value.status==409 and provider.creates==0
    assert service.public('alice','sessions')['sessions']==[]


@pytest.mark.parametrize('image',[None,'latest','example/worker@sha256:invalid',IMAGE_B])
def test_unbound_malformed_or_changed_quote_rejects_before_any_provider_effect(image):
    owned=session(image);provider=FakeRunPod();touches=[]
    if image is None:owned['quote'].pop('worker_image')
    decision=lifecycle(owned,provider,touches=touches).reconcile_session(owned)
    assert decision['error_code']=='worker_image_review_required'
    assert touches==[] and provider.creates==0
    # The follow-up tick confirms provider absence and keeps the rejection reason.
    owned.update(decision)
    final=lifecycle(owned,provider,touches=touches).reconcile_session(owned)
    assert final['cleanup_verified'] is True and final['observed_state']=='terminated'
    assert final['error_code']=='worker_image_review_required'
    assert touches==['provider'] and provider.creates==0


def test_explicit_legacy_path_preserves_old_quote_behavior_and_new_quotes_use_bound_image():
    for legacy in (False,True):
        owned=session();provider=FakeRunPod()
        if legacy:owned['quote'].pop('worker_image')
        controller=lifecycle(owned,provider,legacy=legacy)
        decision=controller.reconcile_session(owned)
        assert decision['observed_state']=='booting' and provider.creates==1
        assert provider.pods['fake1']['image']==IMAGE_A


def test_existing_legacy_or_mismatched_worker_remains_observed_and_cleaned_after_restart():
    for legacy in (False,True):
        owned=session();provider=FakeRunPod()
        if legacy:owned['quote'].pop('worker_image')
        provider.pods['existing']={'id':'existing','name':owned['request_name'],'status':'RUNNING'}
        owned.update(create_attempted=True,provider_ref='existing',observed_state='running',worker_last_heartbeat_at=1000.)
        controller=lifecycle(owned,provider,image=IMAGE_B)
        decision=controller.reconcile_session(owned)
        assert decision['observed_state']=='running' and provider.creates==0
        owned.update(decision,desired_state='terminated')
        restarted=lifecycle(owned,provider,image=IMAGE_B)
        decision=restarted.reconcile_session(owned)
        assert decision['cleanup_verified'] is True and decision['observed_state']=='terminated'
        assert provider.creates==0 and provider.deletes==['existing']


def test_uncertain_legacy_create_keeps_resource_fence_after_image_rollout():
    owned=session();owned['quote'].pop('worker_image');owned.update(create_attempted=True,desired_state='terminated')
    provider=FakeRunPod();controller=lifecycle(owned,provider,image=IMAGE_B)
    decision=controller.reconcile_session(owned)
    assert decision['uncertain_resource_fence'] and not decision['cleanup_verified'] and provider.creates==0
    owned.update(decision)
    provider.pods['late']={'id':'late','name':owned['request_name'],'status':'RUNNING'}
    decision=controller.reconcile_session(owned)
    assert decision['cleanup_verified'] and provider.deletes==['late'] and provider.creates==0


def test_legacy_provider_reference_without_create_flag_never_creates_replacement_after_restart():
    owned=session();owned['quote'].pop('worker_image');owned['provider_ref']='previously-created'
    provider=FakeRunPod();controller=lifecycle(owned,provider,image=IMAGE_B)
    decision=controller.reconcile_session(owned)
    assert decision['error_code']=='create_outcome_pending' and provider.creates==0
    owned.update(decision,desired_state='terminated')
    decision=lifecycle(owned,provider,image=IMAGE_B).reconcile_session(owned)
    assert decision['cleanup_verified'] and provider.creates==0


def test_optional_legacy_catalog_does_not_change_old_api_quote_contract():
    provider=FakeRunPod([dict(id='fake-gpu',manufacturer='NVIDIA',secure=True,memory=24,availability='HIGH',price={'secure':.5})])
    catalog=Catalog(lambda _:provider,disk_usd_per_gb_hour=0,disk_rate_source='fake')
    quote=catalog.quote({'model_id':MODELS[0]['id']},1000.)
    assert 'worker_image' not in quote


def test_saved_only_new_api_can_review_legacy_controller_quote_without_rewriting_it(setup):
    service,provider,_,_=setup
    service.catalog.worker_image=None
    plan=prepare(service)
    assert 'worker_image' not in plan['quote']
    result=approve(service,plan)
    assert result['job']['status']=='queued' and provider.creates==0
    assert 'worker_image' not in service.store.transaction(lambda tx:tx.get('sessions',result['session']['id']))['quote']


def test_malformed_remote_quote_cannot_become_a_prepared_plan(setup):
    service,provider,_,_=setup
    service.catalog.worker_image='latest'
    with pytest.raises(HostedError) as error:prepare(service)
    assert error.value.status==422 and provider.creates==0
    assert service.public('alice','plans')=={'plans':[]}


def test_claimed_creation_keeps_the_quoted_image_even_if_configuration_changes_mid_request():
    owned=session();provider=FakeRunPod();controller=lifecycle(owned,provider)
    def changed(_):
        controller.image=IMAGE_B
        return provider
    controller.provider_factory=changed
    decision=controller.reconcile_session(owned)
    assert decision['observed_state']=='booting'
    assert provider.pods['fake1']['image']==IMAGE_A and controller.image==IMAGE_B
