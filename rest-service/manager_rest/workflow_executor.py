import hashlib
import uuid

from flask_security import current_user

from cloudify.amqp_client import get_client, SendHandler
from cloudify.models_states import PluginInstallationState
from cloudify.constants import (
    MGMTWORKER_QUEUE,
    BROKER_PORT_SSL
)

from manager_rest import config, utils
from manager_rest.storage import get_storage_manager, models


def execute_workflow(execution,
                     bypass_maintenance=None,
                     wait_after_fail=600,
                     handler: SendHandler = None,):
    sm = get_storage_manager()
    token = generate_execution_token(execution)
    context = execution.render_context()
    context.update({
        'wait_after_fail': wait_after_fail,
        'bypass_maintenance': bypass_maintenance,
        'execution_token': token,
        'rest_host': [
            manager.private_ip for manager in sm.list(models.Manager)
        ],
        'rest_token': execution.creator.get_auth_token(),
    })
    if context.get('plugin'):
        managed_plugins = sm.list(models.Plugin, filters={
            'package_name': context['plugin'].get('package_name'),
            'package_version': context['plugin'].get('package_version'),
        }).items
        if managed_plugins:
            context['plugin']['visibility'] = managed_plugins[0].visibility
            context['plugin']['tenant_name'] = managed_plugins[0].tenant_name

    execution_parameters = execution.parameters.copy()
    execution_parameters['__cloudify_context'] = context
    message = {
        'cloudify_task': {'kwargs': execution_parameters},
        'id': execution.id,
        'execution_creator': execution.creator.id
    }

    if handler is not None:
        handler.publish(message)
    else:
        _send_mgmtworker_task(message)


def generate_execution_token(execution):
    sm = get_storage_manager()
    token = uuid.uuid4().hex
    execution.token = hashlib.sha256(token.encode('ascii')).hexdigest()
    sm.update(execution)
    return token


def _get_tenant_dict():
    return {'name': utils.current_tenant.name}


def get_amqp_client(tenant=None):
    vhost = '/' if tenant is None else tenant.rabbitmq_vhost
    client = get_client(
        amqp_host=config.instance.amqp_host,
        amqp_user=config.instance.amqp_username,
        amqp_pass=config.instance.amqp_password,
        amqp_port=BROKER_PORT_SSL,
        amqp_vhost=vhost,
        ssl_enabled=True,
        ssl_cert_path=config.instance.amqp_ca_path
    )
    return client


def workflow_sendhandler() -> SendHandler:
    return SendHandler(MGMTWORKER_QUEUE, 'direct', routing_key='workflow')


def _send_mgmtworker_task(message, exchange=MGMTWORKER_QUEUE,
                          exchange_type='direct', routing_key='workflow'):
    """Send a message to the mgmtworker exchange"""
    client = get_amqp_client()
    send_handler = SendHandler(exchange, exchange_type,
                               routing_key=routing_key)
    client.add_handler(send_handler)
    with client:
        send_handler.publish(message)


def _broadcast_mgmtworker_task(message, exchange='cloudify-mgmtworker-service',
                               exchange_type='fanout', routing_key='service'):
    """Broadcast a message to all mgmtworkers in a cluster."""
    client = get_amqp_client()
    send_handler = SendHandler(exchange, exchange_type,
                               routing_key=routing_key)
    client.add_handler(send_handler)
    with client:
        send_handler.publish(message)


def restart_restservice():
    message = {
        'service_task': {
            'task_name': 'restart-restservice',
            'kwargs': {
                'service_management': config.instance.service_management,
            },
        }
    }
    _broadcast_mgmtworker_task(message)


def cancel_execution(execution):
    sm = get_storage_manager()
    managers = sm.list(models.Manager)
    message = {
        'service_task': {
            'task_name': 'cancel-workflow',
            'kwargs': {
                'rest_host': [manager.private_ip for manager in managers],
                'execution_id': execution.id,
                'rest_token': current_user.get_auth_token(),
                'tenant': _get_tenant_dict(),
                'execution_token': generate_execution_token(execution)
            }
        }
    }
    _broadcast_mgmtworker_task(message)


def _get_plugin_message(plugin, task='install-plugin', target_names=None):
    """Make plugin-related service task message.

    This is for creating plugin install/uninstall messages, to send to
    the mgmtworkers/agents.
    """
    sm = get_storage_manager()
    managers = sm.list(models.Manager)
    message = {
        'service_task': {
            'task_name': task,
            'kwargs': {
                'plugin': plugin.to_dict(),
                'rest_host': [manager.private_ip for manager in managers],
                'rest_token': current_user.get_auth_token(),
                'tenant': _get_tenant_dict(),
            }
        }
    }
    if target_names:
        message['service_task']['kwargs']['target'] = target_names
    return message


def install_plugin(plugin):
    """Send the install-plugin message to agents/mgmtworkers.

    Send the install-plugin message to agents/mgmtworkers that are
    in state==PENDING for that plugin.
    """
    sm = get_storage_manager()
    pstates = sm.list(models._PluginState, filters={
        '_plugin_fk': plugin._storage_id,
        'state': PluginInstallationState.PENDING
    })
    agents_per_tenant = {}
    managers = []
    for pstate in pstates:
        if pstate.manager:
            managers.append(pstate.manager.hostname)
        if pstate.agent:
            agents_per_tenant.setdefault(
                pstate.agent.tenant, []).append(pstate.agent)
    if managers:
        _broadcast_mgmtworker_task(
            _get_plugin_message(plugin, target_names=managers))

    agent_message = _get_plugin_message(plugin)
    if agents_per_tenant:
        for tenant, agents in agents_per_tenant.items():
            # amqp client for the given tenant's vhost.
            # Still use the manager's creds.
            tenant_client = get_amqp_client(tenant)
            with tenant_client:
                for agent in agents:
                    send_handler = SendHandler(
                        agent.name,
                        exchange_type='direct',
                        routing_key='service')
                    tenant_client.add_handler(send_handler)
                    send_handler.publish(agent_message)


def uninstall_plugin(plugin):
    sm = get_storage_manager()
    pstates = sm.list(models._PluginState, filters={
        '_plugin_fk': plugin._storage_id,
        'state': [
            PluginInstallationState.INSTALLED,
            PluginInstallationState.INSTALLING,
            PluginInstallationState.ERROR
        ]
    })
    agents_per_tenant = {}
    managers = []
    for pstate in pstates:
        if pstate.manager:
            managers.append(pstate.manager.hostname)
        if pstate.agent:
            agents_per_tenant.setdefault(
                pstate.agent.tenant, []).append(pstate.agent)
    if managers:
        _broadcast_mgmtworker_task(
            _get_plugin_message(
                plugin, target_names=managers, task='uninstall-plugin'))

    agent_message = _get_plugin_message(plugin, task='uninstall-plugin')
    if agents_per_tenant:
        for tenant, agents in agents_per_tenant.items():
            # amqp client for the given tenant's vhost.
            # Still use the manager's creds.
            tenant_client = get_amqp_client(tenant)
            with tenant_client:
                for agent in agents:
                    send_handler = SendHandler(
                        agent.name,
                        exchange_type='direct',
                        routing_key='service')
                    tenant_client.add_handler(send_handler)
                    send_handler.publish(agent_message)


def delete_source_plugins(deployment_id):
    _broadcast_mgmtworker_task(
        message={
            'service_task': {
                'task_name': 'delete-source-plugins',
                'kwargs': {
                    'deployment_id': deployment_id,
                    'tenant_name': utils.current_tenant.name
                }
            }
        })


def clean_tenant_dirs(tenant_name):
    _broadcast_mgmtworker_task(
        message={
            'service_task': {
                'task_name': 'clean-tenant-dirs',
                'kwargs': {
                    'tenant_name': tenant_name,
                }
            }
        })
