"""Explicit managed MCP assembly. No auto-launch, private-profile or auth fallback.

Deploy with an operator-configured OAuth provider and canonical account mapping;
see HOSTED-BYOK.md. Importing this module makes no network requests. In particular,
the existing private stdio/tunnel entrypoint does not acquire new paid capabilities.
"""
from urllib.parse import urlsplit
import re

from fork_microscope.hosted.credentials import UrllibTransport
from fork_microscope.investigation_bundle import MAX_IMPORT_BYTES
from tools.hosted import ManagedCompute, create_hosted_mcp
from tools.hosted_evidence import HostedEvidenceLibrary


def artifact_reader(controller, *, transport_factory=UrllibTransport):
    """Read only service-created, owner-checked capabilities inside trusted server code."""
    def read(uid, metadata, capability):
        if metadata['storage_mode'] == 'drive':
            ref = capability.get('file_ref')
            if not isinstance(ref, str) or not re.fullmatch(r'df_[0-9a-f]{32}', ref):
                raise ValueError('Invalid saved evidence reference')
            _, chunks = controller.stream('drive/download', {'owner_uid':uid, 'file_ref':ref})
            return chunks
        url = capability.get('url', '')
        p = urlsplit(url)
        if (p.scheme != 'https' or not re.fullmatch(r'[A-Za-z0-9]+-8780\.proxy\.runpod\.net', p.hostname or '')
                or p.port is not None or p.username or p.password or p.query or p.fragment
                or p.path != '/bundles/' + metadata['id']):
            raise ValueError('Invalid saved evidence delivery address')
        token = capability.get('download_token')
        if not isinstance(token, str) or not 20 <= len(token) <= 512 or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError('Invalid saved evidence capability')
        # This is a short-lived artifact capability, never the model-worker or RunPod key.
        response = transport_factory({p.hostname}, max_response_bytes=MAX_IMPORT_BYTES).stream('GET', url,
            {'Authorization':'Bearer '+token, 'Accept':'application/json'}, timeout=60.0)
        if response.status != 200:
            response.close()
            raise ValueError('Saved evidence delivery unavailable')
        def chunks():
            try:
                for chunk in response.chunks:
                    yield chunk
            finally:
                response.close()
        return chunks()
    return read


def assemble_hosted_mcp(public_app, *, auth, owner_resolver, setup_url, durable_evidence_root,
                        durable_storage_confirmed=False):
    """Wire existing public service + controller to authenticated MCP and saved viewer.

    The evidence root must be a durable private volume, not ephemeral Cloud Run /
    tmp storage: a verified save may authorize deletion of the source pod. Explicit
    deployment confirmation is required and is never accepted as a tool argument.
    Auth/owner mapping must make the secure setup page and MCP resolve the same UID.
    """
    if durable_storage_confirmed is not True:
        raise ValueError('Configure and verify durable private evidence storage before enabling hosted saves')
    service = public_app.state.service
    library = HostedEvidenceLibrary(service, durable_root=durable_evidence_root,
                                    artifact_reader=artifact_reader(public_app.state.controller_client))
    facade = ManagedCompute(service, setup_url=setup_url, evidence_library=library)
    return create_hosted_mcp(facade, auth=auth, owner_resolver=owner_resolver)
