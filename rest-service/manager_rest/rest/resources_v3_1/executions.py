#########
# Copyright (c) 2019 Cloudify Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.

import uuid
from datetime import datetime

from flask import request
from sqlalchemy.dialects.postgresql import insert

from cloudify.models_states import ExecutionState
from manager_rest.rest.responses_v3 import ItemsCount

from manager_rest.security import SecuredResource
from manager_rest.security.authorization import authorize
from manager_rest.rest import resources_v2, rest_decorators
from manager_rest.manager_exceptions import (
    BadParametersError, NonexistentWorkflowError)
from manager_rest.resource_manager import get_resource_manager
from manager_rest.rest.rest_utils import (
    get_json_and_verify_params,
    verify_and_convert_bool,
    parse_datetime_multiple_formats
)
from manager_rest.storage import models, db, get_storage_manager, ListResult
from manager_rest.workflow_executor import (
    get_amqp_client,
    workflow_sendhandler
)


class Executions(resources_v2.Executions):
    @authorize('execution_delete')
    @rest_decorators.marshal_with(ItemsCount)
    @rest_decorators.create_filters(models.Execution)
    @rest_decorators.paginate
    @rest_decorators.all_tenants
    def delete(self, filters=None, pagination=None, all_tenants=None):
        request_dict = get_json_and_verify_params({
            'keep_last': {'optional': True, 'type': int},
            'to_datetime': {'optional': True}
        })
        if 'keep_last' in request_dict:
            if 'to_datetime' in request_dict:
                raise BadParametersError(
                    "Must provide either a `to_datetime` timestamp or a "
                    "`keep_last` number of executions to keep"
                )
            if request_dict['keep_last'] <= 0:
                raise BadParametersError(
                    "`keep_last` must be an integer greater than 0. got {} "
                    "instead.".format(request_dict['keep_last'])
                )
        requested_time = None
        if 'to_datetime' in request_dict:
            requested_time = parse_datetime_multiple_formats(
                request_dict['to_datetime'])
        if 'status' in filters:
            if filters['status'] not in ExecutionState.END_STATES:
                raise BadParametersError(
                    'Can\'t filter by execution status `{0}`. '
                    'Allowed statuses are: {1}'.format(
                        filters['status'], ExecutionState.END_STATES)
                )
        else:
            filters['status'] = ExecutionState.END_STATES

        sm = get_storage_manager()
        executions = sm.list(models.Execution,
                             filters=filters,
                             all_tenants=all_tenants,
                             get_all_results=True)
        dep_creation_execs = {}
        for execution in executions:
            if execution.workflow_id == 'create_deployment_environment' and \
                    execution.status == 'terminated':
                dep_creation_execs[execution.deployment_id] = \
                    dep_creation_execs.get(execution.deployment_id, 0) + 1

        deleted_count = 0
        if requested_time:
            for execution in executions:
                creation_time = datetime.strptime(execution.created_at,
                                                  '%Y-%m-%dT%H:%M:%S.%fZ')

                if creation_time < requested_time and \
                        self._can_delete_execution(execution,
                                                   dep_creation_execs):
                    sm.delete(execution)
                    deleted_count += 1
        else:
            if request_dict.get('keep_last'):
                max_to_delete = len(executions) - request_dict['keep_last']
            for execution in executions:
                if self._can_delete_execution(execution, dep_creation_execs):
                    sm.delete(execution)
                    deleted_count += 1
                    if request_dict.get('keep_last') and deleted_count >= \
                            max_to_delete:
                        break
        return ListResult([{'count': deleted_count}],
                          {'pagination': pagination})

    @staticmethod
    def _can_delete_execution(execution, dep_creation_execs):
        if execution.workflow_id == \
                'create_deployment_environment':
            if dep_creation_execs[execution.deployment_id] <= 1:
                return False
            else:
                dep_creation_execs[execution.deployment_id] -= 1
        return True


class ExecutionsCheck(SecuredResource):
    @authorize('execution_should_start')
    def get(self, execution_id):
        """
        `should_start` - return True if this execution can currently start
        (no system exeuctions / executions under the same deployment are
        currently running)
        """

        sm = get_storage_manager()
        execution = sm.get(models.Execution, execution_id)
        deployment_id = execution.deployment.id
        rm = get_resource_manager()
        return not (rm.check_for_executions(deployment_id, force=False,
                                            queue=True, execution=execution))


class ExecutionGroups(SecuredResource):
    @authorize('execution_group_list', allow_all_tenants=True)
    @rest_decorators.marshal_with(models.ExecutionGroup)
    @rest_decorators.sortable(models.ExecutionGroup)
    @rest_decorators.create_filters(models.ExecutionGroup)
    @rest_decorators.paginate
    @rest_decorators.all_tenants
    def get(self, _include=None, filters=None, pagination=None, sort=None,
            all_tenants=None):
        get_all_results = verify_and_convert_bool(
            '_get_all_results',
            request.args.get('_get_all_results', False)
        )
        return get_storage_manager().list(
            models.ExecutionGroup,
            include=_include,
            filters=filters,
            pagination=pagination,
            sort=sort,
            all_tenants=all_tenants,
            get_all_results=get_all_results
        )

    @authorize('execution_group_create')
    @rest_decorators.marshal_with(models.ExecutionGroup, force_get_data=True)
    @rest_decorators.not_while_cancelling
    def post(self):
        request_dict = get_json_and_verify_params({
            'deployment_group_id': {'type': str},
            'workflow_id': {'type': str},
            'default_parameters': {'optional': True},
            'parameters': {'optional': True},
            'force': {'optional': True},
            'concurrency': {'optional': True},
        })
        default_parameters = request_dict.get('default_parameters') or {}
        parameters = request_dict.get('parameters') or {}
        workflow_id = request_dict['workflow_id']
        force = request_dict.get('force') or False
        concurrency = request_dict.get('concurrency', 5)

        sm = get_storage_manager()
        dep_group = sm.get(models.DeploymentGroup,
                           request_dict['deployment_group_id'])
        group = models.ExecutionGroup(
            id=str(uuid.uuid4()),
            deployment_group=dep_group,
            workflow_id=workflow_id,
            visibility=dep_group.visibility,
            concurrency=concurrency,
        )
        sm.put(group)
        rm = get_resource_manager()
        executions = []
        with sm.transaction():
            for dep in dep_group.deployments:
                params = default_parameters.copy()
                params.update(parameters.get(dep.id) or {})
                try:
                    execution = models.Execution(
                        workflow_id=workflow_id,
                        deployment=dep,
                        parameters=params,
                        status=ExecutionState.PENDING,
                    )
                except NonexistentWorkflowError as ex:
                    log = models.Log(
                        reported_timestamp=datetime.utcnow(),
                        message=str(ex),
                        logger='cloudify-restservice',
                        level='info',
                        execution_group=group
                    )
                    sm.put(log)
                else:
                    sm.put(execution)
                    executions.append(execution)
                    group.executions.append(execution)

        amqp_client = get_amqp_client()
        handler = workflow_sendhandler()
        amqp_client.add_handler(handler)
        with amqp_client:
            group.start_executions(sm, rm, handler, force=force)
        return group


class ExecutionGroupsId(SecuredResource):
    @authorize('execution_group_get', allow_all_tenants=True)
    @rest_decorators.marshal_with(models.ExecutionGroup, force_get_data=True)
    @rest_decorators.all_tenants
    def get(self, group_id, _include=None, all_tenants=None):
        return get_storage_manager().get(
            models.ExecutionGroup,
            group_id,
            include=_include,
            all_tenants=all_tenants,
        )

    @authorize('execution_group_cancel')
    @rest_decorators.marshal_with(models.ExecutionGroup)
    def post(self, group_id, **kwargs):
        request_dict = get_json_and_verify_params({'action'})
        action = request_dict['action']

        valid_actions = ['cancel', 'force-cancel', 'kill',
                         'resume', 'force-resume']

        if action not in valid_actions:
            raise BadParametersError(
                'Invalid action: {0}, Valid action values are: {1}'.format(
                    action, valid_actions))

        sm = get_storage_manager()
        group = sm.get(models.ExecutionGroup, group_id)
        if action in ('cancel', 'force-cancel', 'kill'):
            self._cancel_group(sm, group, action)
        if action in ('resume', 'force-resume'):
            self._resume_group(sm, group, action)
        return group

    def _cancel_group(self, sm, group, action):
        rm = get_resource_manager()
        with sm.transaction():
            to_cancel = []
            for exc in group.executions:
                if exc.status == ExecutionState.QUEUED:
                    exc.status = ExecutionState.CANCELLED
                elif exc.status in ExecutionState.END_STATES:
                    continue
                else:
                    to_cancel.append(exc)
            for exc in to_cancel:
                rm.cancel_execution(
                    exc.id,
                    force=action == 'force-cancel',
                    kill=action == 'kill',
                )

    def _resume_group(self, sm, group, action):
        rm = get_resource_manager()
        force = action == 'force-resume'
        resume_states = {ExecutionState.FAILED, ExecutionState.CANCELLED}
        with sm.transaction():
            for exc in group.executions:
                if exc.status not in resume_states:
                    continue
                rm.reset_operations(exc, force=force)
                exc.status = ExecutionState.PENDING
                exc.ended_at = None
                exc.resumed = True
                sm.update(exc, modified_attrs=('status', 'ended_at', 'resume'))

        amqp_client = get_amqp_client()
        handler = workflow_sendhandler()
        amqp_client.add_handler(handler)
        with amqp_client:
            group.start_executions(sm, rm, handler)

    @authorize('execution_group_update')
    @rest_decorators.marshal_with(models.ExecutionGroup)
    def patch(self, group_id, **kwargs):
        request_dict = get_json_and_verify_params({
            'success_group_id': {'optional': True},
            'failure_group_id': {'optional': True},
        })
        sm = get_storage_manager()

        with sm.transaction():
            group = sm.get(models.ExecutionGroup, group_id)
            success_group_id = request_dict.get('success_group_id')
            if success_group_id:
                group.success_group = sm.get(
                    models.DeploymentGroup, success_group_id)
                self._add_deps_to_group(group)
            failure_group_id = request_dict.get('failure_group_id')
            if failure_group_id:
                group.failed_group = sm.get(
                    models.DeploymentGroup, failure_group_id)
                self._add_deps_to_group(group, success=False)
            sm.update(group)
        return group

    def _add_deps_to_group(self, group, success=True):
        deployment_ids = (
            db.session.query(models.Execution._deployment_fk)
            .filter(models.Execution.execution_group_id == group.id)
            .filter(
                models.Execution.status == (
                    ExecutionState.TERMINATED if success
                    else ExecutionState.FAILED
                )
            )
            .all()
        )
        if success:
            target_group_id = group.success_group._storage_id
        else:
            target_group_id = group.failed_group._storage_id
        # low-level sqlalchemy core, to avoid having to fetch all the deps
        tb = models.Deployment.deployment_groups.property.secondary
        db.session.execute(
            insert(tb)
            .values([
                {
                    'deployment_group_id': target_group_id,
                    'deployment_id': dep_id
                } for dep_id, in deployment_ids
            ])
            .on_conflict_do_nothing()
        )
